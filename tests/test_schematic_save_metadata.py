"""A native save timestamp is bound before writing, never globally ignored."""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from test_schematic_attribute_display_native import (
    Collection,
    FakeAttr,
    FakeComponent,
    FakeDesigner,
    _all_writes,
    _operation,
    _parts,
)

from xpedition_cli import native_com_adapter as native
from xpedition_cli.changeset import apply_operations
from xpedition_cli.errors import CLIError
from xpedition_cli.schematic_attribute_display import freeze_save_metadata, verify_display_changes

# Retained 054 native border shape, not an inference from requested geometry.
BEFORE_TIME = "17/10/2026:22:50"
AFTER_TIME = "17/10/2026:23:44"
BORDER_ATTRIBUTES = {
    "@NAME": "Board1",
    "Drawing Revision": "",
    "Drawing Number": "",
    "@SHEETTOTAL": "1",
    "Drawing Author": "",
    "@PRINTORDER": "",
    "@Sheet": "1",
    "Drawing Title": "",
    "@DATETIME": BEFORE_TIME,
}


def _border(uid="$1I1", attributes=None):
    part = FakeComponent("", 0)
    part.UID = uid
    part.Attributes = Collection(
        FakeAttr(name, value) for name, value in (attributes or BORDER_ATTRIBUTES).items()
    )
    part.connections = Collection([])
    part.GetLocation = SimpleNamespace(X=0, Y=0)
    return part


def _timestamp(parts, value, uid="$1I1"):
    border = next(part for part in parts if part.UID == uid)
    next(attr for attr in border.Attributes if attr.Name == "@DATETIME").fields["Value"] = value


def _native_fixture(monkeypatch, extra=None):
    parts = _parts()
    for index, part in enumerate(parts):
        part.UID = f"$1I{100 + index}"
    parts.append(_border())
    if extra is not None:
        parts.append(extra)
    events = []
    app = FakeDesigner(parts, events)
    app.ActiveView.Block.SheetNum = 1
    monkeypatch.setattr(
        native, "_designer_collection", lambda app, method, design: Collection(app.parts)
    )
    monkeypatch.setattr(native, "_sheet_numbers", lambda app: {1: "Board1"})
    monkeypatch.setattr(native, "_PromptAnswerer", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(native, "_settle", lambda seconds: None)
    before = native._designer_snapshot(app, {})
    binding = freeze_save_metadata(before)
    return app, before, binding, events


def _apply(app, binding):
    return native._apply_designer_display_changes(
        app,
        {"project": str(app.project_path), "operations": [_operation()], "save_metadata": binding},
        None,
        "Board1",
    )


def test_retained_border_time_delta_is_rejected_strict_and_reported_only_when_bound(monkeypatch):
    _, before, binding, _ = _native_fixture(monkeypatch)
    after, _ = apply_operations(before, [_operation()])
    after["unnamed_components"][0]["attributes"]["@DATETIME"] = AFTER_TIME
    raw_before, raw_after = copy.deepcopy(before), copy.deepcopy(after)
    assert verify_display_changes(before, after, [_operation()])["valid"] is False
    allowed = verify_display_changes(before, after, [_operation()], save_metadata=binding)
    assert allowed["valid"] is True
    assert allowed["metadata_changes"] == [
        {
            "owner": {"refdes": "", "uid": "$1I1", "sheet": 1},
            "field": "@DATETIME",
            "before": BEFORE_TIME,
            "after": AFTER_TIME,
            "source": "native_save_metadata",
        }
    ]
    assert before == raw_before and after == raw_after


def test_no_observed_timestamp_means_empty_policy_and_strict_comparison(monkeypatch):
    _, before, _, _ = _native_fixture(monkeypatch)
    before["unnamed_components"] = []
    assert freeze_save_metadata(before) is None
    after, _ = apply_operations(before, [_operation()])
    result = verify_display_changes(before, after, [_operation()])
    assert result["valid"] is True and result["metadata_changes"] == []


@pytest.mark.parametrize("number", ["1", 1])
def test_normalised_and_raw_sheet_identifiers_freeze_the_same_owner(monkeypatch, number):
    _, before, binding, _ = _native_fixture(monkeypatch)
    before["sheets"][0]["number"] = number
    assert freeze_save_metadata(before) == binding


@pytest.mark.parametrize("number", [True, 1.0, "01", "1.0", "+1", " 1", "1e0"])
def test_noncanonical_sheet_identifiers_cannot_identify_native_border(monkeypatch, number):
    _, before, _, _ = _native_fixture(monkeypatch)
    before["sheets"][0]["number"] = number
    with pytest.raises(CLIError, match="uniquely observed"):
        freeze_save_metadata(before)


def test_border_timestamp_through_complete_cli_backend_dispatch_normalisation(
    cli, adapter, monkeypatch, tmp_path
):
    import test_schematic_attribute_display_review as chain

    original_parts = chain._parts

    def parts():
        result = original_parts()
        attributes = {**BORDER_ATTRIBUTES, "@SHEETTOTAL": "2"}
        result.append(_border(attributes=attributes))
        return result

    class BorderDesigner(FakeDesigner):
        def save_all(self):
            _timestamp(self.parts, AFTER_TIME)
            super().save_all()

    monkeypatch.setattr(chain, "_parts", parts)
    monkeypatch.setattr(chain, "FakeDesigner", BorderDesigner)
    chain.test_cli_backend_dispatch_chain_has_only_the_one_post_close_open(
        cli, adapter, monkeypatch, tmp_path
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing-name",
        "different-name",
        "missing-total",
        "missing-sheet",
        "sheet-unknown",
        "uid-missing",
        "uid-duplicate",
        "pinned",
        "ambiguous-border",
        "incomplete-values",
    ],
)
def test_timestamp_owner_must_be_uniquely_identified_before_write(monkeypatch, fault):
    _, before, _, _ = _native_fixture(monkeypatch)
    border = before["unnamed_components"][0]
    if fault.startswith("missing-"):
        del border["attributes"][
            ["@NAME", "@SHEETTOTAL", "@Sheet"][
                ["missing-name", "missing-total", "missing-sheet"].index(fault)
            ]
        ]
    elif fault == "different-name":
        border["attributes"]["@NAME"] = "OtherDesign"
    elif fault == "sheet-unknown":
        before["sheets"] = []
    elif fault == "uid-missing":
        border["uid"] = ""
    elif fault == "uid-duplicate":
        border["uid"] = before["components"][0]["uid"]
    elif fault == "pinned":
        border["pins"] = [{"number": "1", "net": None}]
    elif fault == "incomplete-values":
        border["attribute_display_coverage"]["enumeration_complete"] = False
    else:
        other = copy.deepcopy(border)
        other["uid"] = "$1I2"
        before["unnamed_components"].append(other)
    with pytest.raises(CLIError, match="uniquely observed"):
        freeze_save_metadata(before)


@pytest.mark.parametrize(
    "fault",
    [
        "other-owner",
        "other-field",
        "geometry",
        "uid",
        "sheet",
        "missing-time",
        "added-field",
        "added-owner",
    ],
)
def test_bound_timestamp_never_exempts_other_records_or_fields(monkeypatch, fault):
    extra = _border("$1I2", {"@DATETIME": "custom owner, protected"})
    _, before, binding, _ = _native_fixture(monkeypatch, extra)
    after, _ = apply_operations(before, [_operation()])
    border = after["unnamed_components"][0]
    border["attributes"]["@DATETIME"] = AFTER_TIME
    if fault == "other-owner":
        after["unnamed_components"][1]["attributes"]["@DATETIME"] = AFTER_TIME
    elif fault == "other-field":
        border["attributes"]["Drawing Revision"] = "unexpected"
    elif fault == "geometry":
        border["x"] += 1
    elif fault == "uid":
        border["uid"] = "$1I999"
    elif fault == "sheet":
        border["sheet"] = 2
    elif fault == "missing-time":
        del border["attributes"]["@DATETIME"]
    elif fault == "added-field":
        border["attributes"]["Unexpected"] = "new"
    else:
        other = copy.deepcopy(after["unnamed_components"][1])
        other["uid"] = "$1I3"
        after["unnamed_components"].append(other)
    assert (
        verify_display_changes(before, after, [_operation()], save_metadata=binding)["valid"]
        is False
    )


def test_declared_timestamp_can_change_before_save_without_claiming_which_phase_did_it(monkeypatch):
    app, before, binding, events = _native_fixture(monkeypatch)
    snapshot = native._designer_snapshot
    count = 0

    def changing_snapshot(app, params):
        nonlocal count
        count += 1
        if count == 2:
            _timestamp(app.parts, AFTER_TIME)
        return snapshot(app, params)

    monkeypatch.setattr(native, "_designer_snapshot", changing_snapshot)
    result = _apply(app, binding)
    assert result["persistence_verified"] is True
    assert result["verification_before_save"]["metadata_changes"][0]["after"] == AFTER_TIME
    assert events == ["SaveAll", "CloseProject", "OpenProject"]
    assert binding["phase_evidence"] == "edit_plus_save_bundle; individual_phase_not_isolated"
    assert before["unnamed_components"][0]["attributes"]["@DATETIME"] == BEFORE_TIME


@pytest.mark.parametrize(
    "value",
    ["arbitrary text", "2026-10-17 23:44", "31/02/2026:23:44", "17/10/2026:25:44", "", None],
)
def test_bound_timestamp_rejects_non_native_or_invalid_calendar_values(monkeypatch, value):
    _, before, binding, _ = _native_fixture(monkeypatch)
    after, _ = apply_operations(before, [_operation()])
    after["unnamed_components"][0]["attributes"]["@DATETIME"] = value
    assert (
        verify_display_changes(before, after, [_operation()], save_metadata=binding)["valid"]
        is False
    )


def test_pre_save_other_field_change_still_stops_before_save_close_open(monkeypatch):
    app, _, binding, events = _native_fixture(monkeypatch)
    snapshot = native._designer_snapshot
    count = 0

    def changing_snapshot(app, params):
        nonlocal count
        count += 1
        result = snapshot(app, params)
        if count == 2:
            result["unnamed_components"][0]["attributes"]["@DATETIME"] = AFTER_TIME
            result["unnamed_components"][0]["attributes"]["Drawing Revision"] = "unexpected"
        return result

    monkeypatch.setattr(native, "_designer_snapshot", changing_snapshot)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, binding)
    assert caught.value.details["stage"] == "before_save_verification"
    assert caught.value.details["verification"]["metadata_changes"][0]["after"] == AFTER_TIME
    assert events == [] and caught.value.details["saved"] is False


def test_native_only_post_save_time_change_reports_raw_delta_after_real_fake_reopen(monkeypatch):
    app, before, binding, events = _native_fixture(monkeypatch)

    def save():
        _timestamp(app.parts, AFTER_TIME)
        app.save_all()

    app.Documents.SaveAll = save
    result = _apply(app, binding)
    assert events == ["SaveAll", "CloseProject", "OpenProject"]
    assert result["persistence_verified"] is True
    assert result["verification_before_save"]["metadata_changes"] == []
    assert result["metadata_changes"][0]["before"] == BEFORE_TIME
    assert result["metadata_changes"][0]["after"] == AFTER_TIME
    assert (
        result["read_back_before_save"]["unnamed_components"][0]["attributes"]["@DATETIME"]
        == BEFORE_TIME
    )
    assert (
        result["read_back_after_reopen"]["unnamed_components"][0]["attributes"]["@DATETIME"]
        == AFTER_TIME
    )
    assert before["unnamed_components"][0]["attributes"]["@DATETIME"] == BEFORE_TIME


@pytest.mark.parametrize(
    "fault",
    ["missing-binding", "wrong-owner", "wrong-field", "wrong-before", "bool-sheet", "float-sheet"],
)
def test_native_frozen_metadata_scope_mismatch_is_zero_writes(monkeypatch, fault):
    app, _, binding, events = _native_fixture(monkeypatch)
    if fault == "missing-binding":
        binding = None
    elif fault == "wrong-owner":
        binding["owner"]["uid"] = "$1I2"
    elif fault == "wrong-field":
        binding["field"] = "Drawing Title"
    elif fault in {"bool-sheet", "float-sheet"}:
        binding["owner"]["sheet"] = True if fault == "bool-sheet" else 1.0
    else:
        binding["before"] = "different time"
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, binding)
    assert caught.value.details["write_attempted"] is False
    assert _all_writes(app.parts) == [] and events == []


def test_cli_preview_scope_and_native_request_freeze_the_same_actual_border(
    cli, adapter, monkeypatch, tmp_path
):
    from xpedition_cli.cli import schematic

    _, before, binding, _ = _native_fixture(monkeypatch)
    after, _ = apply_operations(before, [_operation()])
    after["unnamed_components"][0]["attributes"]["@DATETIME"] = AFTER_TIME
    reads = iter([before, before, after])
    adapter.on("snapshot", lambda params: next(reads))
    adapter.on(
        "apply_changeset",
        {
            "applied": [{"type": "set_property"}],
            "saved": True,
            "persistence_verified": True,
            "save_acknowledged": True,
            "read_back_after_reopen": after,
        },
    )
    scopes = []
    original = schematic.previewed

    def previewed(preview, scope, **extra):
        scopes.append(copy.deepcopy(scope))
        return original(preview, scope, **extra)

    monkeypatch.setattr(schematic, "previewed", previewed)
    changes = tmp_path / "metadata-display.json"
    changes.write_text(json.dumps({"operations": [_operation()]}), encoding="utf-8")
    project = tmp_path / "Board1.prj"
    project.write_text("offline fixture", encoding="utf-8")
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    code, preview = cli(*args, "--dry-run")
    assert code == 0, preview
    assert preview["data"]["preview"]["save_metadata"] == scopes[0]["save_metadata"] == binding
    assert not any(call["method"] == "apply_changeset" for call in adapter.calls)
    code, result = cli(*args, "--confirm", preview["data"]["confirm_token"])
    assert code == 0, result
    assert adapter.last("apply_changeset")["save_metadata"] == binding
    assert result["data"]["metadata_changes"][0]["before"] == BEFORE_TIME
    assert result["data"]["metadata_changes"][0]["after"] == AFTER_TIME
    assert (
        result["data"]["observed"]["unnamed_components"][0]["attributes"]["@DATETIME"] == AFTER_TIME
    )
