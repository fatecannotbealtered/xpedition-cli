"""Offline counterexamples for the five observed display review blockers."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from test_schematic_attribute_display_native import (
    Collection,
    FakeAttr,
    FakeComponent,
    FakeDesigner,
    _all_writes,
    _apply,
    _harness,
    _operation,
    _parts,
    _snapshot,
)

from xpedition_cli import native_com_adapter as native
from xpedition_cli.backends import native_xpedition
from xpedition_cli.changeset import apply_operations
from xpedition_cli.schematic_attribute_display import verify_display_changes


class CountCollection:
    def __init__(self, count):
        self.Count = count
        self.reads = 0

    def Item(self, index):  # noqa: N802
        self.reads += 1
        return index


@pytest.mark.parametrize("count", [-1, True, False, 1.5, "1", None, 1_000_001])
def test_strict_count_is_not_coerced_or_an_observed_empty_collection(count):
    collection = CountCollection(count)
    with pytest.raises(ValueError, match="strict collection Count"):
        list(native._items(collection, strict=True))
    assert collection.reads == 0


def test_non_strict_count_keeps_legacy_coercion():
    assert list(native._items(CountCollection("1"))) == [1]


@pytest.mark.parametrize("member", ["Name", "Value"])
def test_repeated_named_attribute_getter_failure_is_not_optional_display_coverage(member):
    class IntermittentAttr(FakeAttr):
        def __init__(self):
            super().__init__("Part Number", "preserve")
            self.reads = 0

        def _get(self, key):
            if key == member:
                self.reads += 1
                if self.reads % 2 == 0:
                    raise RuntimeError("repeated identity/value read failed")
            return super()._get(key)

    part = FakeComponent("", 99)
    part.Attributes = Collection([IntermittentAttr()])
    part.connections = Collection([])
    before = _snapshot(_parts())
    before["unnamed_components"].append(native._designer_component_record(part))
    assert (
        before["unnamed_components"][0]["attribute_display_coverage"]["enumeration_complete"]
        is False
    )
    after, _ = apply_operations(before, [_operation()])
    assert verify_display_changes(before, after, [_operation()])["complete"] is False


@pytest.mark.parametrize("count", [None, True, -1, "1", 1.5])
def test_invalid_attribute_count_is_retained_as_incomplete_instead_of_raising(count):
    part = FakeComponent("", 99)
    part.Attributes = CountCollection(count)
    record = native._designer_component_record(part)
    assert record["attribute_display_coverage"]["enumeration_complete"] is False
    assert record["attribute_display_coverage"]["errors"]


@pytest.mark.parametrize("count", [-1, True, 1.5, "1", 1_000_001])
def test_invalid_pin_count_rejects_display_before_any_put_or_save(monkeypatch, count):
    parts = _parts()
    parts[-1].connections = CountCollection(count)
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    assert caught.value.details["write_attempted"] is False
    assert _all_writes(parts) == []
    assert not set(events) & {"SaveAll", "CloseProject", "OpenProject"}
    assert (
        native._designer_component_record(parts[-1])["pin_observation_coverage"]["complete"]
        is False
    )


@pytest.mark.parametrize("fault", ["Name", "Value", "value-drift", "truncated"])
def test_second_attribute_enumeration_must_be_complete_and_match_before_put(monkeypatch, fault):
    part = FakeComponent("R1", 2)
    extra = FakeAttr("Custom", "preserve")
    part.Attributes.append(extra)
    original = native._designer_attributes
    calls = 0

    def changing_attributes(component, owner):
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault in {"Name", "Value"}:
                extra.fail.add(fault)
            elif fault == "value-drift":
                extra.fields["Value"] = "changed outside the request"
            else:
                component.Attributes.extend(FakeAttr(f"Extra{n}", "") for n in range(256))
        return original(component, owner)

    monkeypatch.setattr(native, "_designer_attributes", changing_attributes)
    with pytest.raises(native.AdapterError) as caught:
        native._set_designer_property_display(part, _operation())
    assert calls == 2 and caught.value.details["write_attempted"] is False
    assert all(not attr.writes for attr in part.Attributes)


@pytest.mark.parametrize("name", ["Custom", "Description", ""])
def test_duplicate_original_attribute_name_is_incomplete_instead_of_overwritten(monkeypatch, name):
    parts = _parts()
    parts[-1].Attributes.extend([FakeAttr(name, "first"), FakeAttr(name, "last")])
    record = native._designer_component_record(parts[-1])
    assert record["attributes"][name] == "first"
    assert record["attribute_display_coverage"]["enumeration_complete"] is False
    assert any(
        "duplicate original Name" in error
        for error in record["attribute_display_coverage"]["errors"]
    )
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    assert caught.value.details["write_attempted"] is False
    assert _all_writes(parts) == [] and events == ["snapshot"]


def _with_unnamed(fault=None):
    before = _snapshot(_parts())
    part = FakeComponent("", 99)
    part.UID = "$1I999"
    part.Attributes = Collection([FakeAttr("", "")])
    part.connections = Collection([])
    if fault in {"Name", "Value"}:
        part.Attributes[0].fail.add(fault)
    elif fault == "GetConnections":
        part.GetConnections = lambda: (_ for _ in ()).throw(RuntimeError("getter failed"))
    elif fault == "UID-getter":
        part.UID = lambda: (_ for _ in ()).throw(RuntimeError("getter failed"))
    row = native._designer_component_record(part)
    if fault == "UID-empty":
        row["uid"] = ""
    elif fault == "UID-duplicate-named":
        row["uid"] = before["components"][0]["uid"]
    elif fault == "UID-duplicate-unnamed":
        before["unnamed_components"].append(copy.deepcopy(row))
    elif fault == "missing-identity-coverage":
        del row["identity_observation_coverage"]
    before["unnamed_components"].append(row)
    return before, row


def test_unnamed_border_or_ground_with_empty_name_value_refdes_is_observed():
    before, row = _with_unnamed()
    assert row["refdes"] == row["internal_part_no"] == ""
    assert row["attributes"] == {"": ""}
    assert row["identity_observation_coverage"]["complete"] is True
    after, _ = apply_operations(before, [_operation()])
    assert verify_display_changes(before, after, [_operation()])["valid"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "Name",
        "Value",
        "GetConnections",
        "UID-getter",
        "UID-empty",
        "UID-duplicate-named",
        "UID-duplicate-unnamed",
        "missing-identity-coverage",
    ],
)
def test_identical_unreadable_or_ambiguous_unnamed_records_never_complete(fault):
    before, _ = _with_unnamed(fault)
    after, _ = apply_operations(before, [_operation()])
    result = verify_display_changes(before, after, [_operation()])
    assert result["valid"] is result["complete"] is False


def test_cli_backend_dispatch_chain_has_only_the_one_post_close_open(
    cli, adapter, monkeypatch, tmp_path
):
    """Exercise the handler, real NativeBackend JSON bridge and real dispatch."""
    parts = _parts()
    events = []
    app = FakeDesigner(parts, events)
    app.ActiveView.Block.SheetNum = 1
    requests = []
    attaches = []
    monkeypatch.delenv("XPEDITION_OBSERVE_ONLY", raising=False)
    monkeypatch.delenv("XPEDITION_EXPECTED_ADAPTER_BUILD", raising=False)
    monkeypatch.setattr(
        native,
        "_import_com",
        lambda: (SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None), None),
    )
    monkeypatch.setattr(native, "_heal_gen_py_cache", lambda: None)
    monkeypatch.setattr(native, "_PromptAnswerer", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(native, "_settle", lambda seconds: None)

    def attach(client, attach_only=False):
        attaches.append(attach_only)
        assert attach_only is True
        return app

    monkeypatch.setattr(native, "_viewdraw_application", attach)
    monkeypatch.setattr(
        native, "_designer_collection", lambda app, method, design: Collection(app.parts)
    )
    monkeypatch.setattr(native, "_sheet_numbers", lambda app: {1: "Board1", 2: "Board1"})

    def worker(argv, **kwargs):
        request = json.loads(kwargs["input"])
        requests.append(copy.deepcopy(request))
        inherited = os.environ.get("XPEDITION_OBSERVE_ONLY")
        try:
            try:
                payload = native._response(native.dispatch(request["method"], request["params"]))
            except native.AdapterError as error:
                payload = native._response(error=error)
        finally:
            # Each real adapter request runs in a new process; do not leak its env.
            if inherited is None:
                os.environ.pop("XPEDITION_OBSERVE_ONLY", None)
            else:
                os.environ["XPEDITION_OBSERVE_ONLY"] = inherited
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", worker)
    changes = tmp_path / "display.json"
    changes.write_text(json.dumps({"operations": [_operation()]}), encoding="utf-8")
    args = ["schematic", "edit", "--project", str(app.project_path), "--file", str(changes)]
    code, preview = cli(*args, "--dry-run")
    assert code == 0, preview
    assert events == [] and _all_writes(parts) == []
    code, result = cli(*args, "--confirm", preview["data"]["confirm_token"])
    assert code == 0, result
    assert result["data"]["persistence_verified"] is True
    assert [request["method"] for request in requests] == [
        "snapshot",
        "snapshot",
        "apply_changeset",
        "snapshot",
    ]
    assert all(
        request["params"]["observe_only"] is True
        for request in requests
        if request["method"] == "snapshot"
    )
    assert attaches == [True] * 4
    assert events == ["SaveAll", "CloseProject", "OpenProject"]
    assert [
        result["data"]["lifecycle"][key] for key in ("save_count", "close_count", "reopen_count")
    ] == [1, 1, 1]
