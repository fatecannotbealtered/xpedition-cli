"""Exercise development checker against evidence fraud, drift and scope inflation.

These tests use the pinned spec as a development dependency and never run COM.
Native task test statuses in the design remain not_run even when these tests pass.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "xpedition_development_design_checker", ROOT / "scripts/check_development_design.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.fixture(scope="module")
def carrier():
    return json.loads((ROOT / "development-current-registry.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def native_spec_tools():
    configured = os.environ.get("XPEDITION_DRIVER_SPEC_ROOT")
    if not configured:
        pytest.skip(
            "Design checker requires explicitly selected pinned local spec; "
            "no native work is implied"
        )
    with checker.spec_tools(Path(configured)) as tools:
        yield tools


def test_actual_current_surface_and_generated_preview_remain_distinct(native_spec_tools):
    configured = Path(os.environ["XPEDITION_DRIVER_SPEC_ROOT"])
    report = checker.inspect_design(configured)
    assert report["ok"], report["errors"]
    assert report["compatibility"] == "breaking_migration"
    assert report["counts"]["current_commands"] == 66
    assert report["counts"]["current_handler_declarations"] == 63
    assert report["counts"]["preview_custom_commands"] == 59
    assert report["counts"]["generated_preview_commands"] == 66
    assert report["counts"]["native_runs"] == 0
    assert report["counts"]["native_task_tests_not_run"] == len(carrier_tests())
    for coverage in report["coverage"].values():
        assert coverage["denominator"] == 24
        assert coverage["verified"] == 0
        assert coverage["supported_tasks"] == 0
        assert coverage["declared_command_fcc"] == "not_evaluated_by_this_checker"


def test_current_flag_codec_drift_is_rejected(carrier):
    from xpedition_cli.cli.registry import commands

    runtime = [c.contract() for c in commands()]
    baseline = checker.load(ROOT / "development/current-command-baseline.json")
    changed = copy.deepcopy(runtime)
    move = next(c for c in changed if c["path"] == "pcb placement move")
    next(p for p in move["params"] if p["name"] == "to")["type"] = "json"
    assert checker.compatibility_errors(carrier, baseline, changed)


def test_preview_operation_commands_cannot_silently_become_current(carrier):
    baseline = checker.load(ROOT / "development/compatibility-baseline.json")
    drift = copy.deepcopy(baseline["commands"])
    drift.append({"path": "operation show", "params": []})
    assert checker.compatibility_errors(carrier, baseline, drift)


def test_surveyed_denominator_cannot_be_shrunk_to_exposed_only(carrier, native_spec_tools):
    changed = copy.deepcopy(carrier)
    design = changed["design"]
    exposed = {c["id"] for c in design["capabilities"] if c["cli_exposure"] == "exposed"}
    design["capabilities"] = [c for c in design["capabilities"] if c["id"] in exposed]
    design["scope"]["inventory_ids"] = sorted(exposed)
    design["coverage"]["denominator_ids"] = sorted(exposed)
    # Schema allows a genuinely narrower survey. Our documented 24-group inventory does not.
    official, _, _ = native_spec_tools
    assert not official.check_design(changed, registry_dir=ROOT, require=True)["errors"]
    assert any("denominator" in e for e in checker.source_mapping_errors(changed, ROOT))


def test_forged_artifact_hash_is_rejected(carrier, native_spec_tools):
    changed = copy.deepcopy(carrier)
    changed["design"]["evidence"][0]["sha256"] = "0" * 64
    official, _, _ = native_spec_tools
    errors = official.check_design(changed, registry_dir=ROOT, require=True)["errors"]
    assert any("hash does not match" in e for e in errors)


def test_evidence_path_cannot_escape_registry_directory(carrier, native_spec_tools):
    changed = copy.deepcopy(carrier)
    changed["design"]["evidence"][0]["path"] = "../xpedition-plan/application-inventory.json"
    official, _, _ = native_spec_tools
    errors = official.check_design(changed, registry_dir=ROOT, require=True)["errors"]
    assert any("evidence survey-installation" in e for e in errors)


def test_missing_implementation_entry_is_rejected_without_importing_it(carrier):
    changed = copy.deepcopy(carrier)
    changed["design"]["tasks"][0]["adapter_entries"][0]["entrypoint"] = "does_not_exist"
    assert any(
        "entrypoint does_not_exist absent" in e
        for e in checker.source_mapping_errors(changed, ROOT)
    )


def test_a_real_dispatch_symbol_does_not_validate_an_invented_protocol_method(carrier):
    changed = copy.deepcopy(carrier)
    entry = next(
        e
        for task in changed["design"]["tasks"]
        for e in task["adapter_entries"]
        if " [request.method=" in e["entrypoint"]
    )
    entry["entrypoint"] = "dispatch [request.method=unimplemented_native_task]"
    assert any(
        "request.method unimplemented_native_task absent" in e
        for e in checker.source_mapping_errors(changed, ROOT)
    )


def test_unknown_native_drc_cannot_be_promoted_to_verified(carrier, native_spec_tools):
    changed = copy.deepcopy(carrier)
    cap = next(c for c in changed["design"]["capabilities"] if c["id"] == "c18-pcb-drc")
    cap["testing"] = "verified"
    official, _, _ = native_spec_tools
    errors = official.check_design(changed, registry_dir=ROOT, require=True)["errors"]
    assert any("verified needs" in e for e in errors)


def test_actual_offline_junit_fixture_cannot_count_as_passed_native_test(
    carrier, native_spec_tools
):
    changed = copy.deepcopy(carrier)
    changed["design"]["tests"][0].update(status="passed", evidence_ref="offline-junit")
    official, _, _ = native_spec_tools
    errors = official.check_design(changed, registry_dir=ROOT, require=True)["errors"]
    assert any("passed needs matching real run" in e for e in errors)


def test_supported_task_requires_actual_passing_readback(native_spec_tools):
    changed = checker.load(ROOT / "development-registry.json")
    changed["design"]["tasks"][0]["status"] = "supported"
    official, _, _ = native_spec_tools
    errors = official.check_design(changed, registry_dir=ROOT, require=True)["errors"]
    assert any("supported task lacks actual passing" in e for e in errors)


def test_an_unrelated_spec_cannot_be_used(tmp_path):
    with pytest.raises(ValueError, match="pinned specification artifact"):
        checker.check_spec_pin(tmp_path)


def carrier_tests():
    return checker.load(ROOT / "development-current-registry.json")["design"]["tests"]
