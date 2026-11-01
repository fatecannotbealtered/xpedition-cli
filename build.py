"""Build the self-contained Python binary used by the release workflow, then run it."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import marshal
import os
import platform
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import PyInstaller.__main__
from PyInstaller.archive.readers import CArchiveReader

from xpedition_cli.designer_frozen_binding import (
    GENERATED_MODULE,
    MANIFEST_NAME,
    MAX_FILE_BYTES,
    OWNER_SOURCE_NAMES,
    SCHEME,
    FrozenBindingError,
    collect_bundle_files,
    verify_onedir_bundle,
)

ROOT = Path(__file__).resolve().parent
COMMON_DISTRIBUTIONS = {"Pillow": "12.3.0", "pypdf": "6.18.0"}
FREEZER_DISTRIBUTIONS = {
    "PyInstaller": "6.21.0",
    "pyinstaller-hooks-contrib": "2026.6",
    "altgraph": "0.17.5",
}
WINDOWS_FREEZER_DISTRIBUTIONS = {"pefile": "2024.8.26", "pywin32-ctypes": "0.2.3"}
WINDOWS_DISTRIBUTIONS = {
    "pywin32": "311",
    "pywinauto": "0.6.9",
    "comtypes": "1.4.16",
    "six": "1.17.0",
}
COMMON_MODULES = ["PIL", "PIL.Image", "pypdf", "pypdf.generic"]
WINDOWS_MODULES = [
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "win32com.client.dynamic",
    "win32com.client.gencache",
    "win32com.client.selecttlb",
    "pywinauto",
    "pywinauto.application",
    "pywinauto.controls.uia_controls",
    "comtypes",
    "comtypes.client",
]
PROJECT_MODULES = [
    "xpedition_cli",
    "xpedition_cli.main",
    "xpedition_cli.driver",
    "xpedition_cli.task_readback",
    "xpedition_cli.native_com_adapter",
    "xpedition_cli.native_placement",
    "xpedition_cli.cell_refresh",
    "xpedition_cli.native_pad_mask",
    "xpedition_cli.native_component_candidate",
    "xpedition_cli.operation_budget",
    "xpedition_cli.project_create_budget",
    "xpedition_cli.designer_late_observation",
    "xpedition_cli.design_capabilities",
    "xpedition_cli.design_preflight",
    "xpedition_cli.native_observation_stage",
    "xpedition_cli.native_process",
    "xpedition_cli.session",
    "xpedition_cli.session_ownership",
    "xpedition_cli.pcb_document_guard",
    "xpedition_cli.diagnostic_exit",
    "xpedition_cli.designer_window_identity",
    "xpedition_cli.designer_close_transition",
    "xpedition_cli.designer_existing_views",
    "xpedition_cli.nativeom_ownership",
    "xpedition_cli.designer_nativeom",
    "xpedition_cli.designer_proxy_identity",
    "xpedition_cli.designer_host_bootstrap",
    "xpedition_cli.board_trace",
    "xpedition_cli.board_labels",
    "xpedition_cli.board_unroute",
    "xpedition_cli.board_routepass",
    "xpedition_cli.outline_readback",
    "xpedition_cli.schematic_pdf_artifacts",
    "xpedition_cli.pdf_source_identity",
]
OWNER_PROJECT_MODULES = [
    "xpedition_cli.designer_native_owner",
    "xpedition_cli.designer_owner_transport",
    "xpedition_cli.designer_native_launch",
    "xpedition_cli.designer_startup",
    "xpedition_cli.startup_input_seal",
    "xpedition_cli.execution_lifecycle",
    "xpedition_cli.designer_owner_dispatch",
    "xpedition_cli.designer_source_binding",
    "xpedition_cli.designer_first_open",
    "xpedition_cli.designer_project_admission",
    "xpedition_cli.schematic_edit_protection",
    "xpedition_cli.designer_owner_client",
    "xpedition_cli.designer_owner_recovery",
    "xpedition_cli.designer_driver_routing",
    "xpedition_cli.designer_frozen_binding",
    "xpedition_cli.designer_dual_invoke",
]
OWNER_WINDOWS_PURE_MODULES = [
    "comtypes.automation",
    "comtypes.typeinfo",
    "comtypes.stream",
]
OWNER_WINDOWS_MODULES = ["win32security", "_ctypes", *OWNER_WINDOWS_PURE_MODULES]
PROFILES = ("onefile", "onedir-owner")


def dependency_preflight(*, windows: bool) -> dict:
    """Check the actual freezer environment, without attaching/activating CAD."""
    required = {
        **FREEZER_DISTRIBUTIONS,
        **COMMON_DISTRIBUTIONS,
        **(WINDOWS_FREEZER_DISTRIBUTIONS if windows else {}),
        **(WINDOWS_DISTRIBUTIONS if windows else {}),
    }
    distributions = []
    for name, expected in required.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        distributions.append(
            {"name": name, "expected": expected, "actual": actual, "passed": actual == expected}
        )
    modules = []
    for name in COMMON_MODULES + (WINDOWS_MODULES if windows else []):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError):
            spec = None
        modules.append({"name": name, "found": spec is not None})
    report = {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
            "platform": sys.platform,
        },
        "distributions": distributions,
        "modules": modules,
        "passed": all(row["passed"] for row in distributions)
        and all(row["found"] for row in modules),
        "scope": "Installed build dependencies only; no CAD activation or native acceptance",
    }
    if not report["passed"]:
        raise SystemExit("build dependency preflight failed: " + json.dumps(report))
    return report


def collection_arguments(*, windows: bool, profile: str = "onefile") -> list[str]:
    if profile not in PROFILES:
        raise ValueError("unreviewed_build_profile")
    arguments = []
    modules = COMMON_MODULES + (WINDOWS_MODULES if windows else [])
    if profile == "onedir-owner":
        modules += PROJECT_MODULES + OWNER_PROJECT_MODULES + [GENERATED_MODULE]
        modules += OWNER_WINDOWS_MODULES if windows else []
    for module in dict.fromkeys(modules):
        arguments.extend(["--hidden-import", module])
    for distribution in {**COMMON_DISTRIBUTIONS, **(WINDOWS_DISTRIBUTIONS if windows else {})}:
        # pypdf's dist-info includes its LICENSE. Metadata makes the actual
        # frozen versions independently inspectable instead of inferring them
        # from what happens to be installed on the developer's machine.
        arguments.extend(["--copy-metadata", distribution])
    return arguments


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def owner_sources(source_root: Path = ROOT) -> dict:
    values = {}
    for name in OWNER_SOURCE_NAMES:
        source = source_root / "xpedition_cli" / name
        if not source.is_file() or source.is_symlink() or source.stat().st_size > 1024 * 1024:
            raise SystemExit("Owner build source is missing or exceeds its bound: " + name)
        with source.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise SystemExit("Owner build source exceeds its bound: " + name)
        values[name] = _hash(raw)
    return values


def owner_binding_source(source_root: Path = ROOT) -> bytes:
    """A generated pure module, compiled into PYZ, never a runtime source path."""
    return (
        '"""Build-time source declaration; delivery authentication is external."""\n'
        + "SCHEME = "
        + repr(SCHEME)
        + "\nSOURCES = "
        + repr(dict(sorted(owner_sources(source_root).items())))
        + "\n"
    ).encode("utf-8")


def write_onedir_manifest(binary: Path, *, source_root: Path = ROOT) -> Path:
    value = {
        "scheme": SCHEME,
        "executable": binary.name,
        "source_sha256": owner_sources(source_root),
        "files": collect_bundle_files(binary.parent),
    }
    path = binary.parent / MANIFEST_NAME
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    verify_onedir_bundle(binary, value["source_sha256"])
    return path


def vendor_data_arguments(source_root: Path = ROOT) -> list[str]:
    """Collect only the byte-pinned vendor files, never ignored cache/state."""
    directory = source_root / "xpedition_cli/_vendor"
    lock = directory / "vendor-lock.json"
    files = json.loads(lock.read_text(encoding="utf-8"))["files"]
    arguments = ["--add-data", f"{lock}{os.pathsep}xpedition_cli/_vendor"]
    for relative, expected in sorted(files.items()):
        target = directory / relative
        if (
            not isinstance(relative, str)
            or target.resolve() == directory.resolve()
            or directory.resolve() not in target.resolve().parents
            or target.is_symlink()
            or not target.is_file()
            or _hash(target.read_bytes()) != expected
        ):
            raise SystemExit("Pinned vendor input is missing or differs: " + str(relative))
        destination = (Path("xpedition_cli/_vendor") / Path(relative).parent).as_posix()
        arguments.extend(["--add-data", f"{target}{os.pathsep}{destination}"])
    return arguments


def _code_shape(value):
    """Code equality independent of marshal interning; preserve every code field."""
    if isinstance(value, types.CodeType):
        fields = (
            "co_argcount",
            "co_posonlyargcount",
            "co_kwonlyargcount",
            "co_nlocals",
            "co_stacksize",
            "co_flags",
            "co_code",
            "co_consts",
            "co_names",
            "co_varnames",
            "co_filename",
            "co_name",
            "co_qualname",
            "co_firstlineno",
            "co_linetable",
            "co_exceptiontable",
            "co_freevars",
            "co_cellvars",
        )
        return {key: _code_shape(getattr(value, key)) for key in fields}
    if isinstance(value, (tuple, list)):
        return {"type": type(value).__name__, "items": [_code_shape(item) for item in value]}
    if isinstance(value, frozenset):
        return {
            "type": "frozenset",
            "items": sorted(
                (_code_shape(item) for item in value),
                key=lambda item: json.dumps(item, sort_keys=True),
            ),
        }
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, (float, complex)):
        return {"type": type(value).__name__, "repr": repr(value)}
    if value is Ellipsis:
        return {"type": "Ellipsis"}
    if value is None or type(value) in (str, bool, int):
        return {"type": type(value).__name__, "value": value}
    raise TypeError("Unrecognized code constant type: " + type(value).__name__)


def archive_inventory(
    binary: Path, *, windows: bool, source_root: Path = ROOT, profile: str = "onefile"
) -> dict:
    """Read exact built bytes as data. Never import or execute archive modules."""
    if profile not in PROFILES:
        raise ValueError("unreviewed_build_profile")
    binary = binary.resolve()
    image_before = binary.read_bytes()
    archive = CArchiveReader(str(binary))
    outer, payloads = [], {}
    for name, entry in sorted(archive.toc.items()):
        raw = archive.extract(name)
        normalized = name.replace("\\", "/")
        payloads[normalized] = raw
        outer.append(
            {
                "name": normalized,
                "type": entry[-1],
                "stored_bytes": entry[1],
                "bytes": len(raw),
                "sha256": _hash(raw),
                "length_matches_toc": len(raw) == entry[2],
            }
        )
    pyz_names = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
    checks = []

    def check(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})

    collected, external, binding = [], {}, None
    if profile == "onedir-owner":
        try:
            collected = collect_bundle_files(binary.parent)
            binding = verify_onedir_bundle(binary, owner_sources(source_root))
            check("onedir external payload manifest matches all collected bytes", True)
            for row in collected:
                if row["path"].startswith("_internal/"):
                    external[row["path"][len("_internal/") :]] = binary.parent / row["path"]
        except (FrozenBindingError, OSError, ValueError) as error:
            check(
                "onedir external payload manifest matches all collected bytes",
                False,
                error_type=type(error).__name__,
            )
    payload_names = set(payloads) | set(external)

    def payload(name):
        if name in payloads:
            return payloads[name]
        if name not in external:
            return None
        with external[name].open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise SystemExit("Collected payload exceeds the audited per-file byte limit")
        return data

    check("one embedded PYZ archive", len(pyz_names) == 1)
    pyz_rows, source_rows, pyz = [], [], None
    if len(pyz_names) == 1:
        pyz = archive.open_embedded_archive(pyz_names[0])
        for name, entry in sorted(pyz.toc.items()):
            raw = pyz.extract(name, raw=True)
            pyz_rows.append(
                {
                    "name": name,
                    "type": entry[0],
                    "marshaled_bytes": len(raw) if raw is not None else None,
                    "marshaled_sha256": _hash(raw) if raw is not None else None,
                }
            )
            if name == "xpedition_cli" or name.startswith("xpedition_cli."):
                relative = Path(*name.split("."))
                source = source_root / relative.with_suffix(".py")
                if not source.is_file():
                    source = source_root / relative / "__init__.py"
                embedded = pyz.extract(name)
                matched = False
                source_data = source.read_bytes() if source.is_file() else None
                if isinstance(embedded, types.CodeType) and source_data is not None:
                    compiled = compile(
                        source_data, embedded.co_filename, "exec", dont_inherit=True, optimize=0
                    )
                    matched = _code_shape(embedded) == _code_shape(compiled)
                source_rows.append(
                    {
                        "module": name,
                        "source": source.relative_to(source_root).as_posix(),
                        "source_sha256": _hash(source_data) if source_data is not None else None,
                        "matches_current_source_compile": matched,
                    }
                )
    check("all outer payload lengths match TOC", all(row["length_matches_toc"] for row in outer))
    check("no normalized archive name collisions", len(outer) == len(payloads))
    required = COMMON_MODULES + PROJECT_MODULES + (WINDOWS_MODULES if windows else [])
    if profile == "onedir-owner":
        required += OWNER_PROJECT_MODULES + [GENERATED_MODULE]
        # Native extensions live in COLLECT, not PYZ. Audit their actual .pyd
        # payloads separately instead of demanding fictional Python wrappers.
        required += OWNER_WINDOWS_PURE_MODULES if windows else []
    for name in dict.fromkeys(required):
        check("PYZ module: " + name, pyz is not None and name in pyz.toc)
    if profile == "onedir-owner":
        embedded = pyz.extract(GENERATED_MODULE) if pyz and GENERATED_MODULE in pyz.toc else None
        check(
            "compiled owner source declaration matches the exact build inputs",
            isinstance(embedded, types.CodeType)
            and _code_shape(embedded)
            == _code_shape(
                compile(
                    owner_binding_source(source_root),
                    embedded.co_filename,
                    "exec",
                    dont_inherit=True,
                    optimize=0,
                )
            ),
        )
        entry_matches = False
        entry_data = payloads.get("build_entry")
        if isinstance(entry_data, bytes) and len(entry_data) <= 1024 * 1024:
            try:
                entry = marshal.loads(entry_data)
                entry_matches = isinstance(entry, types.CodeType) and _code_shape(
                    entry
                ) == _code_shape(
                    compile(
                        (source_root / "build_entry.py").read_bytes(),
                        entry.co_filename,
                        "exec",
                        dont_inherit=True,
                        optimize=0,
                    )
                )
            except (OSError, EOFError, ValueError, TypeError):
                entry_matches = False
        check("compiled internal owner entry matches source", entry_matches)
    check(
        "all frozen product modules match source compile",
        bool(source_rows) and all(row["matches_current_source_compile"] for row in source_rows),
        module_count=len(source_rows),
    )
    for relative in (
        "contract/contract.json",
        "contract/driver-v2/product_contract.json",
        "CHANGELOG.md",
    ):
        source = source_root / relative
        check(
            "bundled source data: " + relative,
            source.is_file() and payload(relative) == source.read_bytes(),
        )
    vendor_prefix = "xpedition_cli/_vendor/"
    lock_relative = vendor_prefix + "vendor-lock.json"
    source_lock = source_root / lock_relative
    vendor_files = (
        json.loads(source_lock.read_text(encoding="utf-8"))["files"]
        if source_lock.is_file()
        else {}
    )
    expected_vendor = {vendor_prefix + relative for relative in vendor_files} | {lock_relative}
    actual_vendor = {name for name in payload_names if name.startswith(vendor_prefix)}
    check(
        "exact pinned vendor file set without cache extras",
        bool(vendor_files) and expected_vendor == actual_vendor,
    )
    check(
        "vendor lock bytes match source",
        source_lock.is_file() and payload(lock_relative) == source_lock.read_bytes(),
    )
    for relative, expected in sorted(vendor_files.items()):
        name = vendor_prefix + relative
        check(
            "pinned vendor payload: " + relative,
            name in payload_names and _hash(payload(name)) == expected,
        )
    distributions = {**COMMON_DISTRIBUTIONS, **(WINDOWS_DISTRIBUTIONS if windows else {})}
    for name, expected in distributions.items():
        normalized = name.lower().replace("-", "_")
        metadata_names = [
            key
            for key in payload_names
            if key.lower().startswith(normalized + "-")
            and key.lower().endswith(".dist-info/metadata")
        ]
        versions = [
            next(
                (
                    line[9:]
                    for line in payload(key).decode("utf-8").splitlines()
                    if line.startswith("Version: ")
                ),
                None,
            )
            for key in metadata_names
        ]
        check(
            "frozen distribution version: " + name,
            len(versions) == 1 and versions[0] == expected,
            expected=expected,
            observed=versions,
        )
    check(
        "pypdf LICENSE is bundled",
        any(
            name.startswith("pypdf-")
            and name.endswith(".dist-info/licenses/LICENSE")
            and bool(payload(name))
            for name in payload_names
        ),
    )
    template_name = "xpedition_cli/resources/designer-host-identity.vbs"
    template_source = source_root / template_name
    check(
        "fixed Designer host-bootstrap template matches source",
        template_name in payload_names
        and template_source.is_file()
        and payload(template_name) == template_source.read_bytes(),
    )
    if windows:
        abi = str(sys.version_info.major) + str(sys.version_info.minor)
        for name in (
            f"python{abi}.dll",
            f"pywin32_system32/pythoncom{abi}.dll",
            f"pywin32_system32/pywintypes{abi}.dll",
            "win32/win32api.pyd",
            "win32/win32gui.pyd",
            "win32/win32process.pyd",
        ):
            check("native binary: " + name, name in payload_names)
        if profile == "onedir-owner":
            for name in ("win32/win32security.pyd", "_ctypes.pyd"):
                check("native binary: " + name, name in payload_names)
        check(
            "Pillow native imaging module",
            any(
                name.startswith("PIL/_imaging.") and name.endswith(".pyd") for name in payload_names
            ),
        )
        for hook in ("pyi_rth_pythoncom", "pyi_rth_pywintypes"):
            check("native runtime hook: " + hook, hook in archive.toc)
    check("input executable unchanged during audit", binary.read_bytes() == image_before)
    return {
        "schema_version": "1.0",
        "binary": {"path": str(binary), "bytes": len(image_before), "sha256": _hash(image_before)},
        "build_python": platform.python_version(),
        "pyinstaller_version": importlib.metadata.version("PyInstaller"),
        "source_root": str(source_root.resolve()),
        "profile": profile,
        "collected_payloads": collected,
        "owner_bundle_binding": binding,
        "native_platform": windows,
        "outer_archive": outer,
        "pyz_modules": pyz_rows,
        "source_bindings": source_rows,
        "checks": checks,
        "passed": all(row["passed"] for row in checks),
        "scope": "Actual PyInstaller archive and source equality; no CAD or native acceptance",
    }


def freezer_arguments(output: Path, *, windows: bool, profile="onefile") -> list[str]:
    if profile not in PROFILES:
        raise ValueError("unreviewed_build_profile")
    arguments = [
        str(ROOT / "build_entry.py"),
        "--name",
        "xpedition-cli",
        "--onefile" if profile == "onefile" else "--onedir",
        "--clean",
        "--noconfirm",
        "--paths",
        str(ROOT),
        "--distpath",
        str(output / "dist"),
        "--workpath",
        str(output / "build"),
        "--specpath",
        str(output),
        "--add-data",
        f"{ROOT / 'contract' / 'contract.json'}{os.pathsep}contract",
        "--add-data",
        f"{ROOT / 'CHANGELOG.md'}{os.pathsep}.",
        "--add-data",
        f"{ROOT / 'contract/driver-v2/product_contract.json'}{os.pathsep}contract/driver-v2",
        "--add-data",
        f"{ROOT / 'xpedition_cli/resources/designer-host-identity.vbs'}"
        f"{os.pathsep}xpedition_cli/resources",
    ]
    if profile == "onedir-owner":
        generated = output / "generated"
        generated.mkdir(parents=True, exist_ok=True)
        (generated / (GENERATED_MODULE + ".py")).write_bytes(owner_binding_source())
        arguments += ["--contents-directory", "_internal", "--paths", str(generated)]
    return (
        arguments + collection_arguments(windows=windows, profile=profile) + vendor_data_arguments()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=PROFILES, default="onefile")
    parser.add_argument(
        "--audit-binary",
        type=Path,
        help="Read an existing binary as data; do not rebuild or execute it",
    )
    options = parser.parse_args()
    output = options.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if options.audit_binary:
        report = archive_inventory(
            options.audit_binary, windows=os.name == "nt", profile=options.profile
        )
        destination = output / "archive-inventory.json"
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
        if not report["passed"]:
            raise SystemExit("Archive inventory failed; see " + str(destination))
        print("archive inventory passed: " + str(destination))
        return
    dependencies = dependency_preflight(windows=os.name == "nt")
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(output / ".build-cache"))
    PyInstaller.__main__.run(
        freezer_arguments(output, windows=os.name == "nt", profile=options.profile)
    )
    directory = output / "dist"
    if options.profile == "onedir-owner":
        directory /= "xpedition-cli"
    binary = directory / ("xpedition-cli.exe" if os.name == "nt" else "xpedition-cli")
    if options.profile == "onedir-owner":
        write_onedir_manifest(binary)
    report = archive_inventory(binary, windows=os.name == "nt", profile=options.profile)
    report["dependency_preflight"] = dependencies
    (output / "archive-inventory.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(
            "Actual frozen archive failed completeness/source checks; no smoke qualification"
        )
    smoke(binary)


def smoke(binary: Path) -> None:
    """Run the frozen binary before anything ships it.

    A module the freezer left out fails here, on the build runner, instead of on a
    user's machine: 1.0.1's binaries could run no command, and the release published
    them. Three commands that need no Xpedition reach the handler modules, the
    bundled contract and changelog, the planner and Pillow.
    """
    with tempfile.TemporaryDirectory(prefix="xpedition-offline-smoke-") as folder:
        _smoke(binary.resolve(), Path(folder))


def _smoke(binary: Path, folder: Path) -> None:
    """Test harness authorization is limited to its temporary artifact directory."""
    sys.path.insert(0, str(ROOT))
    from xpedition_cli import __version__

    contract = json.loads(
        (ROOT / "contract/driver-v2/product_contract.json").read_text(encoding="utf-8")
    )
    scope = folder / "scope.json"
    scope.write_text(
        json.dumps(
            {
                "scope_id": "offline-build-fixture",
                "tool": "xpedition-cli",
                "granted_by": "invoked build test harness",
                "granted_at": "2026-10-06T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "artifact"],
                "dangerous": False,
                "roots": [],
                "outputs": [str(folder)],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(folder / "config"),
        "XPEDITION_CLI_SCOPE_FILE": str(scope),
        "PYTHONIOENCODING": "utf-8",
    }

    def run(*args: str) -> dict:
        done = subprocess.run(
            [str(binary), *args, "--compact"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=environment,
        )
        if done.returncode != 0:
            raise SystemExit(f"smoke: `{' '.join(args)}` exited with status {done.returncode}")
        try:
            payload = json.loads(done.stdout)
        except ValueError:
            raise SystemExit(
                f"smoke: `{' '.join(args)}` printed no JSON: {done.stdout[:400]!r} "
                f"{done.stderr[:400]!r}"
            ) from None
        if not payload.get("ok"):
            raise SystemExit(f"smoke: `{' '.join(args)}` failed: {json.dumps(payload)[:600]}")
        return payload["data"]

    version = run("version")["version"]
    if version != __version__:
        raise SystemExit(f"smoke: the binary says {version}, the package {__version__}")
    listed, declared = (
        len(run("reference", "--index")["commands"]),
        len(contract["reference"]["commands"]),
    )
    if listed != declared:
        raise SystemExit(f"smoke: reference lists {listed} commands, the registry {declared}")
    args = (
        "schematic",
        "plan",
        "preview",
        "--design",
        str(ROOT / "examples/demo-sensor-board.json"),
        "--output",
        str(folder / "preview.png"),
    )
    preview = run(*args, "--dry-run")
    receipt = run(*args, "--confirm", preview["confirm_token"])["receipt"]
    drawn = [Path(item["path"]) for item in receipt["extensions"]["result"]["pictures"]]
    if not drawn or not all(path.is_file() for path in drawn):
        raise SystemExit("smoke: the artifact operation created no readable files")
    print(
        f"smoke: {binary} runs version, reference ({listed} commands) "
        "and the offline artifact gate/receipt; native tasks not run"
    )


if __name__ == "__main__":
    main()
