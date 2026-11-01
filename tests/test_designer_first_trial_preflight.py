"""The limited first trial is discoverable before CAD, including real resource refusal."""

from __future__ import annotations

import json

import pytest

from xpedition_cli import project_create_budget as C
from xpedition_cli import schematic_edit_protection as S
from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.designer_source_binding import MAX_BYTES, MAX_FILES
from xpedition_cli.driver import runtime
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A design-before-write declaration must not invoke or probe CAD")

    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(NativeBackend, "status", forbidden)


def public(tmp_path, counts, operations=("project_create", "schematic_edit")):
    path = tmp_path / "new-design-intent.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "counts": counts,
                "operations": list(operations),
            }
        ),
        encoding="utf-8",
    )
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "project",
                "inspect",
                "--preflight",
                "--requirements",
                str(path),
                "--quiet",
            ]
        )
    assert result.envelope["ok"], result.envelope
    assert result.exit_code == 0
    return result.envelope["data"]["preflight"]


def step(report, operation):
    return next(row for row in report["steps"] if row["operation"] == operation)


def test_two_part_trial_reports_create_and_edit_before_opening_any_project(tmp_path):
    report = public(
        tmp_path,
        {
            "setup_source_files": 610,
            "setup_source_bytes": 132655280,
            "components": 2,
            "pins": 4,
            "named_nets": 1,
            "max_component_pins": 2,
            "schematic_operations": 2,
            "schematic_sheets": 1,
        },
    )
    assert report["blocked"] is False
    assert report["can_dispatch"] is report["native_observation_performed"] is False
    create, edit = step(report, "project_create"), step(report, "schematic_edit")
    assert create["command"] == "project create"
    assert create["declared_creation_budget"]["required_seconds"] == 240
    assert create["declared_creation_budget"]["native_hard_cap_seconds"] == 600
    assert edit["command"] == "schematic edit"
    assert edit["all_resources_declared"] is False
    assert "max_component_attributes" in edit["unreported_resources"]
    for row in (create, edit):
        assert row["native_acceptance"] == "not_run" and row["can_dispatch"] is False
        assert row["status"] == "requires_native_observation"
        assert row["operation_budget"]["total_seconds"]["confirm"] > 0
    assert any("unnamed" in item for item in edit["objects"])
    assert any("completed design" in item for item in create["conditions"])
    capacity = report["designer_session_capacity"]
    assert capacity["maximum_commands"] == 128 and capacity["maximum_bytes"] == 128 * 1024**2
    assert capacity["capacity_is_authorization"] is False and capacity["live_usage"] is None


@pytest.mark.parametrize(
    "resource,maximum",
    [
        ("schematic_operations", 64),
        ("components", 4096),
        ("max_component_pins", 512),
        ("max_component_attributes", 256),
        ("schematic_intermediate_points", 64),
        ("schematic_sheets", 512),
        ("schematic_nets", 16384),
        ("schematic_connections", 16384),
        ("schematic_json_values", 200000),
        ("schematic_string_bytes", 8 * 1024**2),
    ],
)
def test_actual_edit_bound_refuses_oversized_intent_without_cad(tmp_path, resource, maximum):
    report = public(tmp_path, {resource: maximum + 1}, ("schematic_edit",))
    edit = step(report, "schematic_edit")
    assert report["blocked"] is True and edit["status"] == "blocked"
    blocker = next(row for row in edit["blockers"] if row["resource"] == resource)
    assert blocker["limit"] == maximum and blocker["declared"] == maximum + 1
    assert edit["can_dispatch"] is False


@pytest.mark.parametrize(
    "resource,maximum",
    [
        ("setup_source_files", MAX_FILES),
        ("setup_source_bytes", MAX_BYTES),
    ],
)
def test_creation_source_extent_bound_is_refused_separately_from_time(tmp_path, resource, maximum):
    report = public(tmp_path, {resource: maximum + 1}, ("project_create",))
    create = step(report, "project_create")
    assert report["blocked"] is True
    assert create["blockers"][0]["code"] == "resource_limit"
    assert create["declared_creation_budget"]["required_seconds"] is None


def test_legal_source_extent_can_still_exceed_actual_creation_time_bound(tmp_path):
    report = public(
        tmp_path,
        {
            "setup_source_files": MAX_FILES,
            "setup_source_bytes": MAX_BYTES,
        },
        ("project_create",),
    )
    create = step(report, "project_create")
    assert all(row["status"] == "within_limit" for row in create["resource_checks"])
    assert report["blocked"] is True and create["declared_creation_budget"]["status"] == "blocked"
    assert create["blockers"][0]["code"] == "project_create_budget_exceeded"
    assert create["blockers"][0]["required_seconds"] > C.HARD_CAP_SECONDS


def test_missing_source_count_is_unobserved_not_zero_or_a_default_qualified_cost(tmp_path):
    report = public(tmp_path, {"setup_source_files": 610}, ("project_create",))
    create = step(report, "project_create")
    assert create["declared_creation_budget"]["required_seconds"] is None
    assert create["declared_creation_budget"]["status"] == "unknown"
    assert create["all_resources_declared"] is False
    assert create["unreported_resources"] == ["setup_source_bytes"]


def test_preflight_uses_the_same_bound_as_the_edit_protector(tmp_path, monkeypatch):
    monkeypatch.setitem(S.LIMITS, "components", 1)
    report = public(tmp_path, {"components": 2}, ("schematic_edit",))
    assert report["blocked"] is True
    assert step(report, "schematic_edit")["blockers"][0]["limit"] == 1


def test_create_declared_cost_cannot_supply_or_replace_real_source_identity():
    source = {
        "scheme": "setup-template-closure-v1",
        "file_count": 610,
        "total_bytes": 132655280,
        "closure_sha256": "a" * 64,
    }
    signed = C.require_cost(source)
    assert signed["required_seconds"] == C.required_seconds(610, 132655280) == 240
    assert signed["source"]["closure_sha256"] == "a" * 64
    with pytest.raises(CLIError) as error:
        C.require_cost({**source, "closure_sha256": None}, signed)
    assert error.value.code == "E_CONFLICT"


@pytest.mark.parametrize("files,size", [(True, 1), (1, True), (0, 1), (1, 0), (MAX_FILES + 1, 1)])
def test_declared_cost_arithmetic_rejects_invalid_typed_extent(files, size):
    with pytest.raises(ValueError):
        C.required_seconds(files, size)
