"""The freezer must prove its actual archive, not infer native support from pip."""

from __future__ import annotations

import importlib.metadata
import json
import marshal
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import build


def failed(report):
    return {row["name"] for row in report["checks"] if not row["passed"]}


def test_preflight_requires_real_pywin32_in_addition_to_pywin32_ctypes(monkeypatch):
    versions = {
        **build.FREEZER_DISTRIBUTIONS,
        **build.WINDOWS_FREEZER_DISTRIBUTIONS,
        **build.COMMON_DISTRIBUTIONS,
        **build.WINDOWS_DISTRIBUTIONS,
    }
    versions.pop("pywin32")

    def version(name):
        if name not in versions:
            raise importlib.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(build.importlib.metadata, "version", version)
    monkeypatch.setattr(build.importlib.util, "find_spec", lambda name: NS(origin="synthetic"))
    with pytest.raises(SystemExit, match="preflight failed") as caught:
        build.dependency_preflight(windows=True)
    report = json.loads(str(caught.value).split(": ", 1)[1])
    actual = {row["name"]: row for row in report["distributions"]}
    assert actual["pywin32-ctypes"]["passed"]
    assert actual["pywin32"]["actual"] is None
    assert not report["passed"]


@pytest.mark.parametrize("name", ["pypdf", "pywin32", "pywinauto", "comtypes", "PyInstaller"])
def test_preflight_rejects_unreviewed_dependency_versions(monkeypatch, name):
    versions = {
        **build.FREEZER_DISTRIBUTIONS,
        **build.WINDOWS_FREEZER_DISTRIBUTIONS,
        **build.COMMON_DISTRIBUTIONS,
        **build.WINDOWS_DISTRIBUTIONS,
        name: "999.0",
    }
    monkeypatch.setattr(build.importlib.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(build.importlib.util, "find_spec", lambda name: NS(origin="synthetic"))
    with pytest.raises(SystemExit, match="preflight failed"):
        build.dependency_preflight(windows=True)


def test_preflight_rejects_missing_import_even_with_matching_dist_metadata(monkeypatch):
    versions = {**build.FREEZER_DISTRIBUTIONS, **build.COMMON_DISTRIBUTIONS}
    monkeypatch.setattr(build.importlib.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(
        build.importlib.util, "find_spec", lambda name: None if name == "pypdf" else object()
    )
    with pytest.raises(SystemExit, match="preflight failed"):
        build.dependency_preflight(windows=False)


def test_collection_explicitly_includes_native_modules_and_parser_metadata():
    args = build.collection_arguments(windows=True)
    hidden = args[1::2]
    assert {
        "pythoncom",
        "pywintypes",
        "win32com.client.gencache",
        "comtypes.client",
        "pywinauto.application",
        "pypdf",
    } <= set(hidden)
    assert "--copy-metadata" in args
    assert "pywin32" in args
    assert "pywin32-ctypes" not in args
    portable = build.collection_arguments(windows=False)
    assert "pythoncom" not in portable
    assert "pypdf" in portable


@pytest.mark.parametrize(
    "module",
    [
        "xpedition_cli.project_create_budget",
        "xpedition_cli.designer_late_observation",
        "xpedition_cli.pdf_source_identity",
    ],
)
def test_client_helpers_are_explicitly_collected_without_changing_owner_source_graph(module):
    args = build.collection_arguments(windows=True, profile="onedir-owner")
    assert module in build.PROJECT_MODULES
    assert any(
        args[index : index + 2] == ["--hidden-import", module] for index in range(len(args) - 1)
    )
    assert module.rsplit(".", 1)[1] + ".py" not in build.OWNER_SOURCE_NAMES


def vendor_fixture(root):
    folder = root / "xpedition_cli/_vendor"
    folder.mkdir(parents=True, exist_ok=True)
    payload = b"pinned test data\r\n"
    (folder / "checked.py").write_bytes(payload)
    (folder / "vendor-lock.json").write_text(
        json.dumps({"files": {"checked.py": build._hash(payload)}}), encoding="utf-8"
    )
    return folder


def test_vendor_collection_uses_only_locked_files_and_ignores_untracked_cache(tmp_path):
    folder = vendor_fixture(tmp_path)
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__/unchecked.pyc").write_bytes(b"not product source")
    args = build.vendor_data_arguments(tmp_path)
    assert len(args) == 4
    assert any("checked.py" in argument for argument in args)
    assert all("__pycache__" not in argument for argument in args)


@pytest.mark.parametrize("mode", ["changed", "missing", "escape"])
def test_vendor_collection_fails_on_bad_pinned_sources(tmp_path, mode):
    folder = vendor_fixture(tmp_path)
    if mode == "changed":
        (folder / "checked.py").write_bytes(b"changed")
    elif mode == "missing":
        (folder / "checked.py").unlink()
    else:
        (folder / "vendor-lock.json").write_text(
            json.dumps({"files": {"../../../escape.py": "a" * 64}}), encoding="utf-8"
        )
    with pytest.raises(SystemExit, match="Pinned vendor input"):
        build.vendor_data_arguments(tmp_path)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    binary = tmp_path / "candidate.exe"
    binary.write_bytes(b"controlled archive reader fixture")
    payloads = {"PYZ.pyz": b"controlled pyz data"}
    code = {}
    for name in build.PROJECT_MODULES:
        relative = Path(*name.split("."))
        source = source_root / relative.with_suffix(".py")
        source.parent.mkdir(parents=True, exist_ok=True)
        data = b"value = 1\n"
        source.write_bytes(data)
        code[name] = compile(data, relative.as_posix() + ".py", "exec", dont_inherit=True)
    for name in build.COMMON_MODULES + build.WINDOWS_MODULES:
        code[name] = compile("pass", name + ".py", "exec", dont_inherit=True)
    for relative in (
        "contract/contract.json",
        "contract/driver-v2/product_contract.json",
        "CHANGELOG.md",
        "xpedition_cli/resources/designer-host-identity.vbs",
    ):
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"controlled pinned data")
        payloads[relative] = path.read_bytes()
    folder = vendor_fixture(source_root)
    payloads["xpedition_cli/_vendor/vendor-lock.json"] = (folder / "vendor-lock.json").read_bytes()
    payloads["xpedition_cli/_vendor/checked.py"] = (folder / "checked.py").read_bytes()
    for name, version in {**build.COMMON_DISTRIBUTIONS, **build.WINDOWS_DISTRIBUTIONS}.items():
        path = name.lower().replace("-", "_") + "-" + version + ".dist-info/METADATA"
        payloads[path] = f"Name: {name}\nVersion: {version}\n".encode()
    payloads["pypdf-6.18.0.dist-info/licenses/LICENSE"] = b"controlled BSD notice"
    abi = str(sys.version_info.major) + str(sys.version_info.minor)
    for name in (
        f"python{abi}.dll",
        f"pywin32_system32/pythoncom{abi}.dll",
        f"pywin32_system32/pywintypes{abi}.dll",
        "win32/win32api.pyd",
        "win32/win32gui.pyd",
        "win32/win32process.pyd",
        f"PIL/_imaging.cp{abi}-win_amd64.pyd",
        "pyi_rth_pythoncom",
        "pyi_rth_pywintypes",
    ):
        payloads[name] = b"controlled native data"

    class Pyz:
        @property
        def toc(self):
            return {name: (0, index, 10) for index, name in enumerate(code)}

        def extract(self, name, raw=False):
            return name.encode() if raw else code[name]

    class Archive:
        @property
        def toc(self):
            return {
                name: (0, len(raw), len(raw), 0, "z" if name == "PYZ.pyz" else "x")
                for name, raw in payloads.items()
            }

        def extract(self, name):
            return payloads[name]

        def open_embedded_archive(self, name):
            assert name == "PYZ.pyz"
            return Pyz()

    monkeypatch.setattr(build, "CArchiveReader", lambda path: Archive())
    return NS(source=source_root, binary=binary, payloads=payloads, code=code)


def inventory(frozen):
    return build.archive_inventory(frozen.binary, windows=True, source_root=frozen.source)


def test_actual_archive_inventory_records_all_hashes_source_bindings_and_dependency_checks(frozen):
    report = inventory(frozen)
    assert report["passed"], failed(report)
    assert len(report["outer_archive"]) == len(frozen.payloads)
    assert len(report["pyz_modules"]) == len(frozen.code)
    assert len(report["source_bindings"]) == len(build.PROJECT_MODULES)
    assert all(row["sha256"] for row in report["outer_archive"])
    assert "no CAD or native acceptance" in report["scope"]


@pytest.mark.parametrize(
    "missing",
    [
        "pythoncom",
        "pywintypes",
        "win32com.client",
        "pypdf",
        "xpedition_cli.task_readback",
        "xpedition_cli.outline_readback",
        "xpedition_cli.project_create_budget",
        "xpedition_cli.designer_late_observation",
    ],
)
def test_archive_gate_rejects_missing_required_module(frozen, missing):
    frozen.code.pop(missing)
    report = inventory(frozen)
    assert not report["passed"]
    assert "PYZ module: " + missing in failed(report)


def test_archive_gate_requires_real_pywin32_metadata_not_ctypes(frozen):
    frozen.payloads.pop("pywin32-311.dist-info/METADATA")
    frozen.payloads["pywin32_ctypes-0.2.3.dist-info/METADATA"] = (
        b"Name: pywin32-ctypes\nVersion: 0.2.3\n"
    )
    report = inventory(frozen)
    assert "frozen distribution version: pywin32" in failed(report)


@pytest.mark.parametrize("version", ["6.17.0", "6.19.0"])
def test_archive_gate_requires_exact_parser_version(frozen, version):
    frozen.payloads["pypdf-6.18.0.dist-info/METADATA"] = (
        f"Name: pypdf\nVersion: {version}\n".encode()
    )
    assert "frozen distribution version: pypdf" in failed(inventory(frozen))


def test_archive_gate_requires_bundled_parser_license(frozen):
    frozen.payloads.pop("pypdf-6.18.0.dist-info/licenses/LICENSE")
    assert "pypdf LICENSE is bundled" in failed(inventory(frozen))


def test_archive_gate_rejects_real_product_source_drift(frozen):
    (frozen.source / "xpedition_cli/task_readback.py").write_text("value = 2\n", encoding="utf-8")
    assert "all frozen product modules match source compile" in failed(inventory(frozen))


@pytest.mark.parametrize(
    "module",
    ["xpedition_cli.project_create_budget", "xpedition_cli.designer_late_observation"],
)
@pytest.mark.parametrize("mode", ["missing_source", "changed_source", "changed_compiled_code"])
def test_client_helper_audit_checks_actual_source_compile_not_only_module_presence(
    frozen, module, mode
):
    source = frozen.source / Path(*module.split(".")).with_suffix(".py")
    if mode == "missing_source":
        source.unlink()
    elif mode == "changed_source":
        source.write_bytes(b"value = 2\n")
    else:
        frozen.code[module] = compile(
            "value = 2\n", frozen.code[module].co_filename, "exec", dont_inherit=True
        )
    report = inventory(frozen)
    assert "PYZ module: " + module not in failed(report)
    assert "all frozen product modules match source compile" in failed(report)
    row = next(row for row in report["source_bindings"] if row["module"] == module)
    assert row["matches_current_source_compile"] is False
    assert not report["passed"]


@pytest.mark.parametrize(
    "module",
    ["xpedition_cli.project_create_budget", "xpedition_cli.designer_late_observation"],
)
def test_current_client_helper_bytes_are_bound_to_frozen_code_without_execution(frozen, module):
    relative = Path(*module.split(".")).with_suffix(".py")
    raw = (build.ROOT / relative).read_bytes()
    (frozen.source / relative).write_bytes(raw)
    frozen.code[module] = compile(raw, relative.as_posix(), "exec", dont_inherit=True, optimize=0)
    report = inventory(frozen)
    assert report["passed"], failed(report)
    row = next(row for row in report["source_bindings"] if row["module"] == module)
    assert row["source_sha256"] == build._hash(raw)
    assert row["matches_current_source_compile"] is True


def test_archive_source_comparison_preserves_boolean_vs_integer_and_all_code_fields():
    assert build._code_shape(compile("value = True", "a", "exec")) != build._code_shape(
        compile("value = 1", "a", "exec")
    )
    assert build._code_shape(compile("value = 1", "a", "exec")) != build._code_shape(
        compile("value = 1", "b", "exec")
    )


@pytest.mark.parametrize("mode", ["cache", "changed_vendor", "missing_source_data"])
def test_archive_gate_rejects_unlocked_vendor_or_source_data(frozen, mode):
    if mode == "cache":
        frozen.payloads["xpedition_cli/_vendor/__pycache__/unchecked.pyc"] = b"ignored cache"
        expected = "exact pinned vendor file set without cache extras"
    elif mode == "changed_vendor":
        frozen.payloads["xpedition_cli/_vendor/checked.py"] = b"bad bytes"
        expected = "pinned vendor payload: checked.py"
    else:
        frozen.payloads.pop("contract/driver-v2/product_contract.json")
        expected = "bundled source data: contract/driver-v2/product_contract.json"
    assert expected in failed(inventory(frozen))


def test_archive_gate_rejects_native_dll_omission_even_when_python_wrapper_exists(frozen):
    abi = str(sys.version_info.major) + str(sys.version_info.minor)
    name = f"pywin32_system32/pythoncom{abi}.dll"
    frozen.payloads.pop(name)
    assert "native binary: " + name in failed(inventory(frozen))


def test_archive_audit_does_not_execute_embedded_code(frozen):
    frozen.code["pypdf"] = compile("raise AssertionError('must never execute')", "pypdf.py", "exec")
    assert inventory(frozen)["passed"]


def test_archive_gate_rejects_normalized_duplicate_paths(frozen):
    frozen.payloads["win32\\win32api.pyd"] = frozen.payloads["win32/win32api.pyd"]
    assert "no normalized archive name collisions" in failed(inventory(frozen))


def test_project_and_build_native_dependency_locks_agree():
    import tomllib

    project = tomllib.loads((build.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    locks = (build.ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    native = project["project"]["optional-dependencies"]["native"]
    for name, version in build.WINDOWS_DISTRIBUTIONS.items():
        assert any(line.startswith(name + "==" + version) for line in native)
        assert name + "==" + version in locks
    assert "pypdf==6.18.0" in project["project"]["dependencies"]
    assert "pypdf==6.18.0" in locks


@pytest.fixture
def onedir(frozen, tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    binary = folder / "xpedition-cli.exe"
    binary.write_bytes(frozen.binary.read_bytes())
    frozen.binary = binary
    for name in build.OWNER_PROJECT_MODULES:
        relative = Path(*name.split("."))
        source = frozen.source / relative.with_suffix(".py")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"value = 1\n")
        frozen.code[name] = compile(
            b"value = 1\n", relative.as_posix() + ".py", "exec", dont_inherit=True
        )
    for name in build.OWNER_WINDOWS_PURE_MODULES:
        frozen.code[name] = compile("pass", name + ".py", "exec")
    frozen.code[build.GENERATED_MODULE] = compile(
        build.owner_binding_source(frozen.source),
        "generated-owner-binding.py",
        "exec",
        dont_inherit=True,
    )
    entry_source = frozen.source / "build_entry.py"
    entry_source.write_bytes(b"value = 1\n")
    frozen.payloads["build_entry"] = marshal.dumps(
        compile(b"value = 1\n", "build_entry.py", "exec", dont_inherit=True)
    )
    for name, raw in list(frozen.payloads.items()):
        if name in {"PYZ.pyz", "pyi_rth_pythoncom", "pyi_rth_pywintypes", "build_entry"}:
            continue
        path = folder / "_internal" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        frozen.payloads.pop(name)
    for name in ("win32/win32security.pyd", "_ctypes.pyd"):
        path = folder / "_internal" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"controlled native payload")
    build.write_onedir_manifest(binary, source_root=frozen.source)
    return frozen


def onedir_inventory(onedir):
    return build.archive_inventory(
        onedir.binary, windows=True, source_root=onedir.source, profile="onedir-owner"
    )


def test_onedir_reads_actual_external_collect_instead_of_inventing_archive_payloads(onedir):
    report = onedir_inventory(onedir)
    assert report["passed"], failed(report)
    assert report["profile"] == "onedir-owner"
    assert len(report["outer_archive"]) == 4
    assert report["owner_bundle_binding"]["payload_file_count"] == len(report["collected_payloads"])
    assert any(
        row["path"] == "_internal/win32/win32security.pyd" for row in report["collected_payloads"]
    )


@pytest.mark.parametrize(
    "missing",
    [
        "xpedition_cli.designer_native_owner",
        "xpedition_cli.designer_first_open",
        "xpedition_cli.designer_project_admission",
        "xpedition_cli.designer_dual_invoke",
        build.GENERATED_MODULE,
        "comtypes.automation",
        "comtypes.typeinfo",
    ],
)
def test_onedir_requires_each_core_module_not_just_those_automatically_found(onedir, missing):
    onedir.code.pop(missing)
    report = onedir_inventory(onedir)
    assert "PYZ module: " + missing in failed(report)


def test_onedir_compiled_literal_must_match_real_source_inputs(onedir):
    onedir.code[build.GENERATED_MODULE] = compile("SOURCES = {}", "generated.py", "exec")
    assert "compiled owner source declaration matches the exact build inputs" in failed(
        onedir_inventory(onedir)
    )


def test_onedir_dual_bridge_source_mismatch_is_not_only_a_module_presence_check(onedir):
    bridge = onedir.source / "xpedition_cli/designer_dual_invoke.py"
    bridge.write_bytes(b"value = 2\n")
    report = onedir_inventory(onedir)
    assert "all frozen product modules match source compile" in failed(report)
    assert "compiled owner source declaration matches the exact build inputs" in failed(report)
    assert not report["passed"]


def test_owner_binding_generation_refuses_missing_dual_bridge_source(onedir):
    (onedir.source / "xpedition_cli/designer_dual_invoke.py").unlink()
    with pytest.raises(SystemExit, match="Owner build source is missing"):
        build.owner_binding_source(onedir.source)


def test_onedir_compiled_internal_entry_must_match_current_source(onedir):
    (onedir.source / "build_entry.py").write_bytes(b"value = 2\n")
    assert "compiled internal owner entry matches source" in failed(onedir_inventory(onedir))


@pytest.mark.parametrize("name", ["win32/win32security.pyd", "_ctypes.pyd"])
def test_onedir_missing_real_native_payload_cannot_pass_with_python_wrapper(onedir, name):
    (onedir.binary.parent / "_internal" / name).unlink()
    report = onedir_inventory(onedir)
    assert not report["passed"]
    assert "onedir external payload manifest matches all collected bytes" in failed(report)


def test_explicit_onedir_collection_and_default_onefile_profile_remain_separate(tmp_path):
    ordinary = build.freezer_arguments(tmp_path / "ordinary", windows=True)
    assert "--onefile" in ordinary and "--onedir" not in ordinary
    assert build.GENERATED_MODULE not in ordinary
    owner = build.freezer_arguments(tmp_path / "dedicated", windows=True, profile="onedir-owner")
    assert "--onedir" in owner and "--onefile" not in owner
    assert owner[owner.index("--contents-directory") + 1] == "_internal"
    for name in (
        build.OWNER_PROJECT_MODULES + build.OWNER_WINDOWS_MODULES + [build.GENERATED_MODULE]
    ):
        assert name in owner
    generated = tmp_path / "dedicated/generated" / (build.GENERATED_MODULE + ".py")
    assert generated.read_bytes() == build.owner_binding_source()
