"""Offline build gates must not turn archive success into CAD/release evidence."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("check_release_native_build")
packager = load("package_native_artifact")


@pytest.fixture
def windows(monkeypatch):
    # Do not mutate global sys.platform: importing a module on Linux after that
    # would choose Windows-only dependencies in otherwise offline tests.
    monkeypatch.setattr(
        gate,
        "sys",
        SimpleNamespace(platform="win32", version_info=(3, 12, 10), maxsize=2**63 - 1),
    )
    monkeypatch.setattr(gate.platform, "python_implementation", lambda: "CPython")


@pytest.mark.parametrize(
    "profile,layout",
    [
        ("onefile", "single-executable"),
        ("onefile", "whole-onedir"),
        ("onedir-owner", "single-executable"),
    ],
)
def test_legacy_or_partial_transport_refuses_before_dependency_or_archive_reads(
    monkeypatch, windows, profile, layout
):
    monkeypatch.setitem(sys.modules, "build", None)
    result = gate.check(profile=profile, archive_layout=layout, phase="archive", binary="no.exe")
    assert result["ok"] is False
    assert result["dependencies"] is result["archive"] is None
    assert result["native_task_acceptance"] == "not_run"
    assert result["release_qualification"] == "unqualified"


@pytest.mark.parametrize(
    "field,value",
    [("platform", "linux"), ("version_info", (3, 11, 9)), ("maxsize", 2**31 - 1)],
)
def test_unreviewed_runner_refuses(monkeypatch, windows, field, value):
    monkeypatch.setattr(gate.sys, field, value)
    result = gate.check(profile="onedir-owner", archive_layout="whole-onedir")
    assert not result["ok"]


def test_route_success_is_not_dependency_archive_cad_or_release_success(windows):
    result = gate.check(profile="onedir-owner", archive_layout="whole-onedir")
    assert result["ok"]
    assert result["dependencies"] is result["archive"] is None
    assert result["native_task_acceptance"] == "not_run"
    assert result["release_qualification"] == "unqualified"


def test_dependency_failure_is_bounded_without_claiming_observed_archive(monkeypatch, windows):
    def failed(**kwargs):
        assert kwargs == {"windows": True}
        raise SystemExit("diagnostic from build")

    monkeypatch.setitem(sys.modules, "build", SimpleNamespace(dependency_preflight=failed))
    result = gate.check(profile="onedir-owner", archive_layout="whole-onedir", phase="dependencies")
    assert not result["ok"]
    assert result["archive"] is None


@pytest.mark.parametrize("passed", [False, True])
def test_archive_gate_uses_actual_inventory_result_not_file_presence(monkeypatch, windows, passed):
    seen = []

    def inventory(binary, **kwargs):
        seen.append((binary, kwargs))
        return {
            "passed": passed,
            "binary": {"sha256": "a" * 64},
            "profile": "onedir-owner",
            "native_platform": True,
            "collected_payloads": [{"path": "xpedition-cli.exe"}],
            "source_bindings": [{"module": "fixture"}],
            "checks": [{"name": "PYZ module: pythoncom", "passed": passed}],
            "owner_bundle_binding": {"payload_file_count": 1},
        }

    monkeypatch.setitem(sys.modules, "build", SimpleNamespace(archive_inventory=inventory))
    result = gate.check(
        profile="onedir-owner",
        archive_layout="whole-onedir",
        phase="archive",
        binary="candidate.exe",
    )
    assert seen == [(Path("candidate.exe"), {"windows": True, "profile": "onedir-owner"})]
    assert result["ok"] is passed
    assert result["native_task_acceptance"] == "not_run"
    assert result["release_qualification"] == "unqualified"


def test_archive_phase_requires_explicit_binary(monkeypatch, windows):
    monkeypatch.setitem(sys.modules, "build", SimpleNamespace())
    result = gate.check(profile="onedir-owner", archive_layout="whole-onedir", phase="archive")
    assert not result["ok"]


def test_gate_cli_returns_one_document_and_nonzero_for_incomplete_artifact(monkeypatch, capsys):
    monkeypatch.setattr(gate, "check", lambda **kwargs: {"ok": False})
    assert gate.main(["--profile", "onefile", "--archive-layout", "single-executable"]) == 2
    assert json.loads(capsys.readouterr().out) == {"ok": False}


def test_archive_read_error_is_structured_and_does_not_echo_external_text(monkeypatch, capsys):
    def failed(**kwargs):
        raise RuntimeError("untrusted-secret-path")

    monkeypatch.setattr(gate, "check", failed)
    code = gate.main(["--profile", "onedir-owner", "--archive-layout", "whole-onedir"])
    raw = capsys.readouterr().out
    assert code == 2
    assert "untrusted-secret-path" not in raw
    assert json.loads(raw)["error_type"] == "RuntimeError"


def test_packaging_read_error_is_structured_and_does_not_echo_external_text(monkeypatch, capsys):
    def failed(*args):
        raise RuntimeError("secretvalue")

    monkeypatch.setattr(packager, "package", failed)
    code = packager.main(["--binary", "fixture.exe", "--output", "new-folder"])
    raw = capsys.readouterr().out
    assert code == 2
    assert "secretvalue" not in raw
    assert json.loads(raw)["reason"] is None


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / "_internal/pywin32_system32").mkdir(parents=True)
    binary = root / "xpedition-cli.exe"
    binary.write_bytes(b"fixture executable, never executed")
    (root / "_internal/pywin32_system32/pythoncom312.dll").write_bytes(b"fixture native payload")
    (root / "_internal/.hidden-payload").write_bytes(b"must not be skipped")
    (root / packager.MANIFEST_NAME).write_text("fixture audited manifest", encoding="utf-8")
    rows = packager.collect_bundle_files(root)
    inventory = {
        "passed": True,
        "binary": {"bytes": binary.stat().st_size, "sha256": packager._sha(binary.read_bytes())},
        "collected_payloads": rows,
        "checks": [{"name": "fixture audit", "passed": True}],
    }
    calls = []

    def audit(path, **kwargs):
        calls.append((path, kwargs))
        return copy.deepcopy(inventory)

    monkeypatch.setitem(sys.modules, "build", SimpleNamespace(archive_inventory=audit))
    monkeypatch.setattr(packager, "_git_source", lambda: "a" * 40)
    return SimpleNamespace(root=root, binary=binary, inventory=inventory, calls=calls)


def test_packager_preserves_every_native_hidden_and_manifest_byte(bundle, tmp_path):
    output = tmp_path / "new-artifact"
    result = packager.package(bundle.binary, output)
    assert result["ok"]
    assert result["whole_bundle_zip_verified"] is result["input_bundle_unchanged"] is True
    assert result["native_task_acceptance"] == "not_run"
    assert result["release_qualification"] == "unqualified"
    assert not result["trial_launcher_included"]
    assert bundle.calls == [(bundle.binary, {"windows": True, "profile": "onedir-owner"})]
    with zipfile.ZipFile(output / result["archive"]["name"]) as zipped:
        assert {
            "xpedition-cli/xpedition-cli.exe",
            "xpedition-cli/_internal/pywin32_system32/pythoncom312.dll",
            "xpedition-cli/_internal/.hidden-payload",
            "xpedition-cli/" + packager.MANIFEST_NAME,
        } == set(zipped.namelist())
        for row in result["payload_files"]:
            assert (
                zipped.read("xpedition-cli/" + row["path"])
                == (bundle.root / row["path"]).read_bytes()
            )


@pytest.mark.parametrize("mode", ["failed", "changed_payload", "missing_payload"])
def test_packager_rejects_failed_or_stale_audit_before_creating_output(bundle, tmp_path, mode):
    if mode == "failed":
        bundle.inventory["passed"] = False
    elif mode == "changed_payload":
        (bundle.root / "_internal/.hidden-payload").write_bytes(b"changed")
    else:
        (bundle.root / "_internal/.hidden-payload").unlink()
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="audit_failed|changed_after_archive_audit"):
        packager.package(bundle.binary, output)
    assert not output.exists()


@pytest.mark.parametrize("mode", ["existing_output", "inside_bundle"])
def test_packager_never_overwrites_or_adds_files_to_input_bundle(bundle, tmp_path, mode):
    output = bundle.root / "new" if mode == "inside_bundle" else tmp_path / "existing"
    if mode == "existing_output":
        output.mkdir()
        (output / "keep.txt").write_bytes(b"keep")
    before = packager.collect_bundle_files(bundle.root)
    with pytest.raises(ValueError, match="outside_bundle|must_be_new"):
        packager.package(bundle.binary, output)
    assert packager.collect_bundle_files(bundle.root) == before
    assert bundle.calls == []


def test_zip_with_only_executable_cannot_be_reported_complete(bundle, tmp_path, monkeypatch):
    original = packager.zipfile.ZipFile

    class MissingPayload(original):
        def writestr(self, name, *args, **kwargs):
            if name != "xpedition-cli/xpedition-cli.exe":
                return None
            return super().writestr(name, *args, **kwargs)

    monkeypatch.setattr(packager.zipfile, "ZipFile", MissingPayload)
    output = tmp_path / "incomplete-zip"
    with pytest.raises(ValueError, match="zip_member_set_mismatch"):
        packager.package(bundle.binary, output)
    assert not (output / "NATIVE-ARTIFACT.json").exists()


def test_zip_crc_failure_cannot_be_reported_complete(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(packager.zipfile.ZipFile, "testzip", lambda self: "corrupt-member")
    output = tmp_path / "bad-crc"
    with pytest.raises(ValueError, match="zip_crc_mismatch"):
        packager.package(bundle.binary, output)
    assert not (output / "NATIVE-ARTIFACT.json").exists()


def test_mid_packaging_source_change_cannot_be_reported_complete(bundle, tmp_path, monkeypatch):
    original = packager._read
    calls = 0

    def changed(path, limit):
        nonlocal calls
        calls += 1
        raw = original(path, limit)
        if calls == 2:
            return raw + b"unobserved bytes"
        return raw

    monkeypatch.setattr(packager, "_read", changed)
    output = tmp_path / "source-drift"
    with pytest.raises(ValueError, match="source_changed_while_packaging"):
        packager.package(bundle.binary, output)
    assert not (output / "NATIVE-ARTIFACT.json").exists()


def test_post_packaging_source_change_cannot_be_reported_complete(bundle, tmp_path, monkeypatch):
    original = packager.collect_bundle_files
    calls = 0

    def changed(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (bundle.root / "_internal/.hidden-payload").write_bytes(b"changed after copy")
        return original(root)

    monkeypatch.setattr(packager, "collect_bundle_files", changed)
    output = tmp_path / "late-drift"
    with pytest.raises(ValueError, match="source_changed_after_packaging"):
        packager.package(bundle.binary, output)
    assert not (output / "NATIVE-ARTIFACT.json").exists()


def test_bounded_file_read_refuses_growth(tmp_path):
    path = tmp_path / "grew"
    path.write_bytes(b"12345")
    with pytest.raises(ValueError, match="exceeds_observed_size"):
        packager._read(path, 4)


def test_release_keeps_readiness_and_explicitly_rejects_legacy_transport():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    readiness = workflow.index("scripts/check_driver_contract.py --release")
    route = workflow.index(
        "scripts/check_release_native_build.py --profile onefile --archive-layout single-executable"
    )
    assert readiness < route < workflow.index("run: python build.py")
    assert "continue-on-error" not in workflow


def test_native_artifact_workflow_is_manual_readonly_whole_onedir_and_not_release():
    workflow = (ROOT / ".github/workflows/native-artifact.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow and "pull_request:" not in workflow
    assert "contents: read" in workflow and "id-token:" not in workflow
    assert 'python-version: "3.12.10"' in workflow
    assert "pip install -r requirements.txt -r requirements-build.txt" in workflow
    assert "build.py --profile onedir-owner --output-root native-build" in workflow
    assert (
        "scripts/package_native_artifact.py "
        "--binary native-build/dist/xpedition-cli/xpedition-cli.exe"
    ) in workflow
    assert "path: native-artifact/*" in workflow
    assert "action-gh-release" not in workflow and "npm publish" not in workflow
    assert "--release" not in workflow
