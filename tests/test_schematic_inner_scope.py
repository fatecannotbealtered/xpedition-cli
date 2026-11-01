"""Inner edit confirmation uses the same complete native census canonicalization."""

# Imported pytest fixtures are injected through the test parameters.
# ruff: noqa: F811
import copy
import json

import pytest
from test_native_snapshot_revision import attribute_native
from test_schematic_edit_durability_cli import edit_scene, no_native  # noqa: F401

from xpedition_cli import confirm, native_snapshot_revision
from xpedition_cli.cli import schematic as S
from xpedition_cli.driver import _stable
from xpedition_cli.errors import CLIError


def scope(design):
    return {
        "operation": "schematic_edit",
        "project": "private-project.prj",
        "design_hash": S._snapshot_hash(design),
        "operations": [{"type": "set_property", "refdes": "Q94", "name": "Value", "value": "22k"}],
        "save_metadata": {"field": "@DATETIME", "before": "private-save-metadata"},
    }


def test_inner_and_outer_use_one_helper_and_permutation_retains_all_original_rows(monkeypatch):
    before = attribute_native()
    original = copy.deepcopy(before)
    after = copy.deepcopy(before)
    after["components"][0]["attribute_display"].reverse()
    calls = []
    actual = native_snapshot_revision.canonical

    def shared(value):
        calls.append(value)
        return actual(value)

    monkeypatch.setattr(native_snapshot_revision, "canonical", shared)
    assert _stable(before) == _stable(after)
    assert S._snapshot_hash(before) == S._snapshot_hash(after)
    assert len(calls) == 4
    assert before == original
    token, _ = confirm.issue(scope(before))
    confirm.consume(token, scope(after))


@pytest.mark.parametrize(
    "field", ["value", "x", "y", "font", "size", "origin", "visible", "component_x"]
)
def test_real_non_target_value_geometry_or_font_still_invalidates_inner_confirmation(field):
    before = attribute_native()
    after = copy.deepcopy(before)
    row = after["components"][0]["attribute_display"][0]
    if field == "value":
        row["value"] = "changed-non-target-value"
    elif field in {"x", "y", "size", "origin"}:
        row["display"][field] += 1
    elif field == "font":
        row["display"][field] = "different-font"
    elif field == "visible":
        row["display"][field] = False
    else:
        after["components"][0]["x"] += 1
    after["components"][0]["attribute_display"].reverse()
    assert S._snapshot_hash(before) != S._snapshot_hash(after)
    token, _ = confirm.issue(scope(before))
    with pytest.raises(CLIError, match="confirmation token does not match"):
        confirm.consume(token, scope(after))


@pytest.mark.parametrize("fault", ["duplicate", "case_ambiguous", "missing"])
def test_ambiguous_attribute_identity_does_not_produce_an_inner_hash(fault):
    design = attribute_native()
    rows = design["components"][0]["attribute_display"]
    if fault == "missing":
        del rows[0]["name"]
    else:
        rows[1]["name"] = rows[0]["name"].lower() if fault == "case_ambiguous" else rows[0]["name"]
    with pytest.raises(ValueError):
        S._snapshot_hash(design)


@pytest.mark.parametrize(
    "fault", ["enumeration_complete", "errors", "truncated", "attribute_count"]
)
def test_incomplete_census_cannot_gain_inner_order_neutrality(fault):
    before = attribute_native()
    before["components"][0]["attribute_display_coverage"][fault] = {
        "enumeration_complete": False,
        "errors": ["getter incomplete"],
        "truncated": True,
        "attribute_count": 999,
    }[fault]
    after = copy.deepcopy(before)
    after["components"][0]["attribute_display"].reverse()
    assert S._snapshot_hash(before) != S._snapshot_hash(after)


@pytest.mark.parametrize("field", ["operation", "project", "operations", "save_metadata"])
def test_all_other_scope_bindings_remain_required(field):
    before = scope(attribute_native())
    current = copy.deepcopy(before)
    current[field] = "changed" if field in {"operation", "project"} else []
    token, _ = confirm.issue(before)
    with pytest.raises(CLIError, match="confirmation token does not match"):
        confirm.consume(token, current)


def test_real_edit_handler_accepts_complete_readback_permutation_and_still_verifies_edit(
    edit_scene,
):
    case = edit_scene
    count = [0]

    def snapshot(_params):
        count[0] += 1
        value = copy.deepcopy(case.before if count[0] <= 2 else case.fresh)
        if count[0] == 2:
            for part in value["components"]:
                part["attribute_display"].reverse()
        return value

    case.adapter.on("snapshot", snapshot)
    code, preview = case.preview()
    assert code == 0, preview
    code, result = case.confirm(preview["data"]["confirm_token"])
    assert code == 0, result
    assert result["data"]["persistence_verified"] is True
    assert count[0] == 3 and case.adapter.methods().count("apply_changeset") == 1


def test_inner_scope_diagnostic_has_only_current_fixed_field_types_and_lengths(edit_scene):
    case = edit_scene
    code, preview = case.preview()
    assert code == 0
    changed = copy.deepcopy(case.operation)
    changed["value"] = "DO_NOT_EXPOSE_OPERATION_VALUE"
    operation_file = case.project.parent / "operations.json"
    operation_file.write_text(json.dumps({"operations": [changed]}))
    code, result = case.confirm(preview["data"]["confirm_token"])
    assert code != 0
    details = result["error"]["details"]
    assert details["confirmation_layer"] == "schematic_handler"
    assert details["engineering_write_dispatched"] is False
    summary = details["scope_diagnostic"]
    assert summary["expected_fields"] == "unobserved"
    assert set(summary["current_fields"]) == {
        "operation",
        "project",
        "design_hash",
        "operations",
        "save_metadata",
    }
    assert "DO_NOT_EXPOSE" not in json.dumps(summary)
    assert "ct_" not in json.dumps(summary) and str(case.project) not in json.dumps(summary)
    assert "apply_changeset" not in case.adapter.methods()
