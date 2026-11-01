"""Bounded package bytes and internal-entry tests; no freezer, COM or CAD."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

import build
import build_entry
from xpedition_cli import designer_frozen_binding as F
from xpedition_cli import designer_native_owner as owner


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)

    def forbidden(*a, **kw):
        hits.append(True)
        pytest.fail("Frozen binding tests must not build, start workers or call native APIs")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    monkeypatch.setattr(build.PyInstaller.__main__, "run", forbidden)
    monkeypatch.setattr(build.subprocess, "Popen", forbidden)
    yield
    assert hits == []


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    directory = tmp_path / "bundle"
    directory.mkdir()
    binary = directory / "xpedition-cli.exe"
    binary.write_bytes(b"controlled executable bytes, not an executable")
    internal = directory / "_internal"
    internal.mkdir()
    (internal / "fixed.dat").write_bytes(b"fixed test data")
    sources = {name: "a" * 64 for name in F.OWNER_SOURCE_NAMES}
    manifest = {
        "scheme": F.SCHEME,
        "executable": binary.name,
        "source_sha256": sources,
        "files": F.collect_bundle_files(directory),
    }
    path = directory / F.MANIFEST_NAME
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setitem(sys.modules, F.GENERATED_MODULE, NS(SCHEME=F.SCHEME, SOURCES=sources))
    return NS(root=directory, binary=binary, sources=sources, path=path, manifest=manifest)


def test_binding_reads_actual_payload_and_never_claims_authentication(bundle):
    facts = F.frozen_owner_binding()
    assert facts["executable_sha256"] == hashlib.sha256(bundle.binary.read_bytes()).hexdigest()
    assert facts["compiled_source_sha256"] == bundle.sources
    assert facts["payload_file_count"] == 2
    assert facts["payload_manifest_sha256"] == hashlib.sha256(bundle.path.read_bytes()).hexdigest()
    assert "authority" not in facts and "authenticated" not in facts
    assert str(bundle.root) not in json.dumps(facts)


@pytest.mark.parametrize("mode", ["extra", "changed", "missing", "source", "unsafe", "duplicate"])
def test_changed_or_incomplete_bundle_rejects_without_native_entry(bundle, mode):
    if mode == "extra":
        (bundle.root / "extra.dat").write_bytes(b"unlisted")
    elif mode == "changed":
        bundle.binary.write_bytes(b"changed executable")
    elif mode == "missing":
        (bundle.root / "_internal/fixed.dat").unlink()
    else:
        manifest = copy.deepcopy(bundle.manifest)
        if mode == "source":
            manifest["source_sha256"][F.OWNER_SOURCE_NAMES[0]] = "b" * 64
        elif mode == "unsafe":
            manifest["files"][0]["path"] = "../foreign.dat"
        else:
            manifest["files"].append(manifest["files"][0])
        bundle.path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(F.FrozenBindingError):
        F.frozen_owner_binding()


@pytest.mark.parametrize("mode", ["source", "onefile", "missing_compiled", "invalid_compiled"])
def test_unqualified_packaging_never_passes_the_internal_entry(bundle, monkeypatch, mode):
    if mode == "source":
        monkeypatch.setattr(sys, "frozen", False)
    elif mode == "onefile":
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle.root / "_MEI123"))
    elif mode == "missing_compiled":
        monkeypatch.delitem(sys.modules, F.GENERATED_MODULE)
    else:
        monkeypatch.setitem(sys.modules, F.GENERATED_MODULE, NS(SCHEME=F.SCHEME, SOURCES={}))
    with pytest.raises(F.FrozenBindingError):
        F.frozen_owner_binding()


@pytest.mark.parametrize(
    "limit", ["MAX_FILES", "MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_MANIFEST_BYTES"]
)
def test_all_payload_and_manifest_caps_fail_closed(bundle, monkeypatch, limit):
    monkeypatch.setattr(F, limit, 1)
    with pytest.raises(F.FrozenBindingError):
        F.frozen_owner_binding()


def test_internal_entry_only_dispatches_canonical_owner_after_binding(bundle, monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "xpedition_cli.designer_native_owner",
        NS(main=lambda args: calls.append(args) or 17),
    )
    args = ["--internal-designer-owner", "--root", "private-fixture", "--epoch", "d" * 32]
    assert build_entry.main(args) == 17
    assert calls == [["--owned-child", *args[1:]]]
    assert sys.dont_write_bytecode is True


@pytest.mark.parametrize(
    "args",
    [
        ["--internal-designer-owner"],
        ["--internal-designer-owner", "--root", "r", "--epoch", "invalid"],
        ["--internal-designer-owner", "--root", "r", "--epoch", "d" * 32, "extra"],
    ],
)
def test_internal_entry_malformed_shape_has_no_owner_import(bundle, monkeypatch, args):
    monkeypatch.setitem(sys.modules, "xpedition_cli.designer_native_owner", None)
    assert build_entry.main(args) == 21


def test_internal_entry_changed_payload_has_no_owner_import(bundle, monkeypatch):
    bundle.binary.write_bytes(b"changed")
    monkeypatch.setitem(sys.modules, "xpedition_cli.designer_native_owner", None)
    assert build_entry.main(["--internal-designer-owner", "--root", "r", "--epoch", "d" * 32]) == 21


def test_ordinary_cli_entry_preserves_argv_and_has_no_owner_binding(monkeypatch):
    calls = []
    monkeypatch.setattr(F, "frozen_owner_binding", lambda: pytest.fail("Not an owner entry"))
    monkeypatch.setitem(sys.modules, "xpedition_cli.main", NS(main=lambda a: calls.append(a) or 0))
    assert build_entry.main(["version"]) == 0
    assert calls == [["version"]]


def test_frozen_source_map_preserves_historical_shape_and_binds_all_payloads(bundle):
    source = owner._source_binding()
    assert set(source) == set(F.OWNER_SOURCE_NAMES) | {
        "@frozen/executable",
        "@frozen/payload_manifest",
        "@frozen/payload_tree",
    }
    assert {name: source[name] for name in F.OWNER_SOURCE_NAMES} == bundle.sources
    assert all(isinstance(value, str) and len(value) == 64 for value in source.values())
    assert source == F.frozen_source_binding()  # Parent/child/client use the same producer.
    bundle.binary.write_bytes(b"changed after parent observation")
    with pytest.raises(F.FrozenBindingError):
        owner._source_binding()


def test_source_map_has_exact_current_native_sources_without_frozen_metadata(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    values = owner._source_binding()
    assert set(values) == set(F.OWNER_SOURCE_NAMES)
    assert "native_com_adapter.py" in values  # Includes configured-license inheritance.
    assert "designer_dual_invoke.py" in values
    assert not any(name.startswith("@frozen/") for name in values)


@pytest.mark.parametrize("mode", ["missing", "changed"])
def test_compiled_dual_bridge_hash_must_match_manifest_before_owner_entry(
    bundle, monkeypatch, mode
):
    values = dict(bundle.sources)
    if mode == "missing":
        values.pop("designer_dual_invoke.py")
    else:
        values["designer_dual_invoke.py"] = "b" * 64
    monkeypatch.setitem(sys.modules, F.GENERATED_MODULE, NS(SCHEME=F.SCHEME, SOURCES=values))
    with pytest.raises(F.FrozenBindingError):
        F.frozen_owner_binding()


def test_source_binding_reads_bridge_bytes_and_refuses_missing_file(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    folder = tmp_path / "synthetic-source"
    folder.mkdir()
    for name in F.OWNER_SOURCE_NAMES:
        (folder / name).write_bytes(b"controlled source observation, never imported")
    monkeypatch.setattr(owner, "__file__", str(folder / "designer_native_owner.py"))
    first = owner._source_binding()
    bridge = folder / "designer_dual_invoke.py"
    bridge.write_bytes(b"changed bridge bytes")
    changed = owner._source_binding()
    assert changed[bridge.name] == hashlib.sha256(bridge.read_bytes()).hexdigest()
    assert changed[bridge.name] != first[bridge.name]
    assert all(changed[name] == first[name] for name in first if name != bridge.name)
    bridge.unlink()
    with pytest.raises(owner.OwnerError, match="dedicated_native_launcher_not_integrated"):
        owner._source_binding()


def test_frozen_owner_starts_same_executable_with_fixed_entry_and_reset(
    bundle, monkeypatch, tmp_path
):
    root = tmp_path / "fresh-owner"
    seen, closed = [], []
    monkeypatch.setattr(owner.L, "_plain_path", lambda value: value)
    monkeypatch.setattr(owner.L, "_private_mkdir", lambda value: value.mkdir())
    monkeypatch.setattr(owner.L, "_identity", lambda handle: {"alive": True, "pid": 100})
    monkeypatch.setattr(owner, "observe_owner", lambda *args, **kwargs: {"status": "unknown"})

    def detached(arguments, **options):
        seen.append((arguments, options))
        return NS(_handle=101, close_parent_handle=lambda: closed.append(True))

    monkeypatch.setattr(owner.L, "_DetachedChild", detached)
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "0")
    monkeypatch.setenv("MGLS_LICENSE_FILE", "synthetic-license-preserved")
    plan = {
        "scheme": "designer-native-empty-launch-v1",
        "installation_root": "D:/synthetic/XENTP2604/SDD_HOME",
        "runtime_directory": str(root / "startup"),
    }
    result = owner.launch_measurement_owner(root, plan, observation_seconds=0)
    args, options = seen[0]
    assert args == [
        str(bundle.binary),
        "--internal-designer-owner",
        "--root",
        str(root),
        "--epoch",
        result["ticket"].epoch,
    ]
    assert options["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert options["env"]["MGLS_LICENSE_FILE"] == "synthetic-license-preserved"
    assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "0"
    assert not any(value in args for value in ("-X", "-B", "--owned-child"))
    key = (root / "integrity-key.bin").read_bytes()
    intent, _ = owner.L._read(root / "intent.json", key)
    assert intent["source_sha256"] == F.frozen_source_binding()
    assert closed == [True]


def test_onefile_still_refuses_owner_before_runtime_creation(bundle, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle.root / "_MEIunqualified"))
    monkeypatch.setattr(owner.L, "_plain_path", lambda value: value)
    root = tmp_path / "never-created"
    plan = {
        "scheme": "designer-native-empty-launch-v1",
        "installation_root": "D:/synthetic/XENTP2604/SDD_HOME",
        "runtime_directory": str(root / "startup"),
    }
    with pytest.raises(F.FrozenBindingError, match="independent_onefile_owner_unqualified"):
        owner.launch_measurement_owner(root, plan, observation_seconds=0)
    assert not root.exists()


def test_external_child_copy_strips_bundle_selectors_but_preserves_licenses(bundle):
    internal = bundle.root / "_internal"
    original = {
        "Path": os.pathsep.join(
            [
                str(internal),
                str(internal / "pywin32_system32"),
                "D:/synthetic/vendor/bin",
                "C:/Windows/System32",
            ]
        ),
        "MGLS_LICENSE_FILE": "",
        "SALT_LICENSE_SERVER": "synthetic-configured-value",
        "WDIR": "private-input;private-work;vendor-standard",
        "_PYI_ARCHIVE_FILE": "synthetic-private",
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
        "PYTHONPATH": "synthetic-python-path",
    }
    before = copy.deepcopy(original)
    result = F.external_child_environment(original)
    assert result["Path"].split(os.pathsep) == ["D:/synthetic/vendor/bin", "C:/Windows/System32"]
    assert result["MGLS_LICENSE_FILE"] == ""
    assert result["SALT_LICENSE_SERVER"] == original["SALT_LICENSE_SERVER"]
    assert result["WDIR"] == original["WDIR"] and original == before
    assert not any(key.upper().startswith(("_PYI_", "PYINSTALLER_", "PYTHON")) for key in result)


@pytest.mark.parametrize("failure", [None, "spawn", "read", "reset", "restore"])
def test_external_dll_search_is_bounded_and_restored_on_spawn_failure(bundle, monkeypatch, failure):
    calls = []
    previous = str(bundle.root / "_internal")

    def get(size, buffer):
        calls.append("get")
        if failure == "read":
            return 0
        buffer.value = previous
        return len(previous)

    def set_directory(value):
        calls.append(value)
        return not (
            (value is None and failure == "reset") or (value is not None and failure == "restore")
        )

    monkeypatch.setattr(
        F, "_dll_directory_api", lambda: NS(GetDllDirectoryW=get, SetDllDirectoryW=set_directory)
    )
    if failure:
        expected = RuntimeError if failure == "spawn" else F.FrozenBindingError
        with pytest.raises(expected):
            with F.external_application_dll_search():
                calls.append("spawn")
                if failure == "spawn":
                    raise RuntimeError("synthetic spawn failure")
    else:
        with F.external_application_dll_search():
            calls.append("spawn")
    if failure == "read":
        assert calls == ["get"]
    elif failure == "reset":
        assert calls == ["get", None]
    else:
        assert calls == ["get", None, "spawn", previous]


def test_source_external_spawn_environment_and_dll_search_are_inert(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(
        F, "_dll_directory_api", lambda: pytest.fail("Source must not change DLL search")
    )
    original = {"PATH": "existing", "PYTHONPATH": "unchanged", "MGLS_LICENSE_FILE": ""}
    assert F.external_child_environment(original) == original
    with F.external_application_dll_search():
        pass


@pytest.mark.parametrize("failure", [None, "spawn", "restore"])
def test_actual_launch_boundary_restores_dll_search_and_retains_dispatched_lease(
    bundle, monkeypatch, failure
):
    from test_designer_native_launch import ENDPOINT, EPOCH, PLAN, _launch_inputs_fixture

    from xpedition_cli import designer_native_launch as launch
    from xpedition_cli import native_com_adapter as adapter

    events, inputs = _launch_inputs_fixture(monkeypatch)
    monkeypatch.setattr(adapter, "_inherit_license_environment", lambda: None)
    previous, directory, calls = str(bundle.root / "_internal"), [None], []

    def get(size, buffer):
        buffer.value = previous
        return len(previous)

    def set_directory(value):
        directory[0] = value
        calls.append(value)
        return not (failure == "restore" and value is not None)

    monkeypatch.setattr(
        F, "_dll_directory_api", lambda: NS(GetDllDirectoryW=get, SetDllDirectoryW=set_directory)
    )
    spawn = launch.subprocess.Popen

    def checked_spawn(arguments, **options):
        assert directory[0] is None
        assert not any(name.upper().startswith("_PYI_") for name in options["env"])
        if failure == "spawn":
            raise OSError("synthetic creation failure")
        return spawn(arguments, **options)

    monkeypatch.setattr(launch.subprocess, "Popen", checked_spawn)
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "synthetic-inherited-value")
    if failure:
        with pytest.raises(launch.LaunchError) as caught:
            launch.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
        if failure == "restore":
            assert caught.value.dispatched is True
            assert caught.value.lease.process is not None
            assert "spawn" in events
        else:
            assert caught.value.lease.process is None
        assert not inputs[0].released  # Actual terminal cleanup is still the owner's duty.
    else:
        lease = launch.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
        assert lease.process is not None and "original_popen_bound" in events
    assert calls == [None, previous]
    assert os.environ["_PYI_ARCHIVE_FILE"] == "synthetic-inherited-value"
