"""Developer measurement output never exports a private connection capability."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_private_epoch_is_removed_recursively_without_rewriting_raw_evidence():
    path = Path(__file__).resolve().parents[1] / "scripts/native_acceptance/designer_measurement.py"
    spec = importlib.util.spec_from_file_location("designer_measurement_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = {
        "command_id": "owner-command-fixture",
        "initial_observation": {"original_outcome": "unknown", "epoch": "private-fixture-epoch"},
        "observations": [{"endpoint": "private-fixture-endpoint", "received": True}],
        "open_project_matches_scope": True,
    }
    result = module.safe(original)
    assert "private-fixture" not in json.dumps(result)
    assert result["initial_observation"]["original_outcome"] == "unknown"
    assert result["observations"] == [{"received": True}]
    assert result["open_project_matches_scope"] is True
    assert original["initial_observation"]["epoch"] == "private-fixture-epoch"


@pytest.fixture
def snapshot(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/native_acceptance/designer_measurement.py"
    spec = importlib.util.spec_from_file_location("measurement_staged_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = tmp_path / "new_case"
    source = case / "source"
    files = {}
    for name in (
        "xpedition_cli/__init__.py",
        "xpedition_cli/designer_native_owner.py",
        "xpedition_cli/designer_native_launch.py",
        "xpedition_cli/startup_input_seal.py",
        "contract/driver-v2/product_contract.json",
    ):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = ("fixed offline source " + name).encode()
        target.write_bytes(raw)
        files[name] = hashlib.sha256(raw).hexdigest()
    binding = {
        "base_commit": "a" * 40,
        "working_files": files,
        "exe_used": False,
        "public_engineering_qualification": False,
    }
    (case / "source-binding.json").write_text(json.dumps(binding), encoding="utf-8")
    return module, case, source, binding


def test_complete_source_is_verified_before_import(snapshot):
    module, case, source, _ = snapshot
    assert module.validate_staged_source(case) == source.resolve()


@pytest.mark.parametrize("damage", ["missing", "changed", "extra", "missing-required"])
def test_incomplete_or_changed_snapshot_is_not_a_fallback(snapshot, damage):
    module, case, source, binding = snapshot
    target = source / "xpedition_cli/designer_native_owner.py"
    if damage == "missing":
        target.unlink()
    elif damage == "changed":
        target.write_bytes(b"changed source")
    elif damage == "extra":
        (source / "xpedition_cli/unbound.py").write_bytes(b"unbound")
    else:
        binding["working_files"].pop("xpedition_cli/startup_input_seal.py")
        (case / "source-binding.json").write_text(json.dumps(binding), encoding="utf-8")
    with pytest.raises(ValueError, match="staged_source_"):
        module.validate_staged_source(case)


@pytest.mark.parametrize(
    "member", ["../escape.py", "xpedition_cli/../escape.py", "C:/elsewhere.py"]
)
def test_manifest_cannot_point_outside_its_snapshot(snapshot, member):
    module, case, _, binding = snapshot
    binding["working_files"][member] = "b" * 64
    (case / "source-binding.json").write_text(json.dumps(binding), encoding="utf-8")
    with pytest.raises(ValueError, match="staged_source_member_invalid"):
        module.validate_staged_source(case)


def test_cached_package_outside_snapshot_is_rejected(snapshot, monkeypatch):
    module, _, source, _ = snapshot
    monkeypatch.setattr(
        module,
        "sys",
        SimpleNamespace(
            modules={
                "xpedition_cli.designer_native_owner": SimpleNamespace(__file__="D:/other/owner.py")
            }
        ),
    )
    with pytest.raises(ValueError, match="nonstaged_package_import_refused"):
        module.require_staged_imports(source)
    module.sys.modules["xpedition_cli.designer_native_owner"].__file__ = str(
        source / "xpedition_cli/designer_native_owner.py"
    )
    module.require_staged_imports(source)


def test_launch_without_stage_refuses_before_any_package_import(snapshot, monkeypatch):
    module, case, _, _ = snapshot
    (case / "source-binding.json").unlink()
    monkeypatch.setattr(module, "require_staged_imports", lambda *a: pytest.fail("Import reached"))
    with pytest.raises(FileNotFoundError):
        module.main(["launch-new-project", "--workspace", str(case.parent), "--case", case.name])


def test_existing_command_observer_keeps_id_when_older_snapshot_reader_raises(snapshot):
    module, *_ = snapshot
    command_id = "owner-command-" + "b" * 32

    def incomplete(*args, **kwargs):
        raise ValueError("execution_snapshot_not_complete")

    owner = SimpleNamespace(
        observe_owner_command=incomplete,
        submit_owner_command=lambda *a: pytest.fail("Read must not replay"),
    )
    answer = module.observe_command_safely(owner, object(), command_id, 0)
    assert answer["command_id"] == command_id and answer["status"] == "unknown"
    assert answer["observation"] == "unobserved" and answer["request_replayed"] is False


def test_existing_command_observer_returns_same_id_and_redacts_epoch(snapshot):
    module, *_ = snapshot
    command_id = "owner-command-" + "b" * 32
    owner = SimpleNamespace(
        observe_owner_command=lambda *a, **k: {
            "command_id": command_id,
            "initial_observation": {"epoch": "private", "original_outcome": "unknown"},
            "observations": [{"phase": "command_returned"}],
        }
    )
    answer = module.safe(module.observe_command_safely(owner, object(), command_id, 0.1))
    assert answer["command_id"] == command_id and answer["request_replayed"] is False
    assert "epoch" not in answer["initial_observation"]
