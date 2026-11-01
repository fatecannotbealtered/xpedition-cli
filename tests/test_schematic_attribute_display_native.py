"""Offline display writes through native-shaped attributes, before any SaveAll."""

from __future__ import annotations

import copy
import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as native
from xpedition_cli.changeset import apply_operations
from xpedition_cli.cli.schematic import native_operations
from xpedition_cli.errors import CLIError
from xpedition_cli.schematic_attribute_display import DISPLAY_NAMES, VALUE_VISIBLE


class Collection(list):
    @property
    def Count(self):  # noqa: N802 - native COM member
        return len(self)

    def Item(self, index):  # noqa: N802 - native COM member
        return self[index - 1]


class FakeAttr:
    """IVdAttr shape: GetLocation returns a point; Visible is an integer property."""

    def __init__(self, name="Value", value="4.7k"):
        self.fields = {
            "Name": name,
            "Value": value,
            "Visible": 0,
            "Size": 10,
            "Origin": 1,
            "Orientation": 0,
        }
        self.point = SimpleNamespace(X=120, Y=240)
        self.writes = []
        self.fail = set()
        self.fail_after_write = set()
        self.ignore = set()
        self.location_reads = 0

    def _get(self, member):
        if member in self.fail or (self.writes and member in self.fail_after_write):
            raise RuntimeError(f"fixture {member} getter unavailable")
        if member not in self.fields:
            raise AttributeError(member)
        return self.fields[member]

    def _set(self, member, value):
        self.writes.append((member, value))
        if member not in self.ignore:
            self.fields[member] = value

    @property
    def Name(self):  # noqa: N802
        return self._get("Name")

    @property
    def Value(self):  # noqa: N802
        return self._get("Value")

    @Value.setter
    def Value(self, value):  # noqa: N802
        self._set("Value", value)

    @property
    def Visible(self):  # noqa: N802
        return self._get("Visible")

    @Visible.setter
    def Visible(self, value):  # noqa: N802
        self._set("Visible", value)

    @property
    def Size(self):  # noqa: N802
        return self._get("Size")

    @Size.setter
    def Size(self, value):  # noqa: N802
        self._set("Size", value)

    @property
    def Origin(self):  # noqa: N802
        return self._get("Origin")

    @property
    def Orientation(self):  # noqa: N802
        return self._get("Orientation")

    def GetLocation(self):  # noqa: N802
        self.location_reads += 1
        if "GetLocation" in self.fail or (self.writes and "GetLocation" in self.fail_after_write):
            raise RuntimeError("fixture location getter unavailable")
        return self.point

    def SetLocation(self, x, y):  # noqa: N802
        self.writes.append(("SetLocation", x, y))
        if "SetLocation" not in self.ignore:
            self.point = SimpleNamespace(X=x, Y=y)


class FakeComponent:
    def __init__(self, refdes, index):
        self.Refdes = refdes
        self.UID = f"${index % 2 + 1}I{100 + index}"
        self.Orientation = 0
        self.GetLocation = SimpleNamespace(X=100 + 50 * index, Y=200)
        self.Attributes = Collection(
            [FakeAttr("Value"), FakeAttr("Part Label", refdes), FakeAttr("Part Number", "part")]
        )
        self.connections = Collection(
            [
                SimpleNamespace(
                    CompPin=SimpleNamespace(
                        Number=str(number), GetLocation=SimpleNamespace(X=100, Y=200 + number)
                    ),
                    Net=SimpleNamespace(LogicalNetName=net),
                )
                for number, net in ((1, "SIGNAL"), (2, "GND"))
            ]
        )

    def GetConnections(self):  # noqa: N802
        return self.connections

    def FindAttribute(self, name):  # noqa: N802
        pytest.fail(f"display edits must enumerate existing attributes, not FindAttribute({name})")

    def AddAttribute(self, *args):  # noqa: N802
        pytest.fail("display edits must never create attributes")


def _parts():
    return [
        FakeComponent(ref, index)
        for index, ref in enumerate(("U1", "U2", "R1", "R2", "R3", "R4", "C1", "C2"))
    ]


def _snapshot(parts):
    return {
        "project": "offline-fixture",
        "revision": "native",
        "metadata": {"fixture": True},
        "sheets": [{"name": "sheet1"}, {"name": "sheet2"}],
        "components": [native._designer_component_record(part) for part in parts],
        "unnamed_components": [],
        "nets": [{"name": "SIGNAL"}, {"name": "GND"}],
        "connections": [
            {"net": "SIGNAL", "pins": [f"{part.Refdes}.1" for part in parts]},
            {"net": "GND", "pins": [f"{part.Refdes}.2" for part in parts]},
        ],
    }


def _operation(name="PartLabel", *, size=None):
    display = {"x": 233, "y": 377, "visible": True}
    if size is not None:
        display["size"] = size
    return {
        "type": "set_property",
        "refdes": "R1",
        "name": name,
        "value": "RC label",
        "display": display,
    }


def _owner():
    return {"refdes": "R1", "uid": "$1I102", "sheet": 1}


def _all_writes(parts):
    return [write for part in parts for attr in part.Attributes for write in attr.writes]


class FakeTypeInfo:
    """Pure Python type-info declaration for the real application_identity reader."""

    names = ["IsProjectOpened", "GetProjectData", "ProcessID"]

    def GetContainingTypeLib(self):  # noqa: N802
        return (
            SimpleNamespace(
                GetLibAttr=lambda: ("offline-guid", 0, 0, 1, 0, 0),
                GetDocumentation=lambda index: ("Offline Designer Fixture",),
            ),
            0,
        )

    def GetTypeAttr(self):  # noqa: N802
        return SimpleNamespace(cFuncs=len(self.names))

    def GetFuncDesc(self, index):  # noqa: N802
        return (index, 0, 0, 0, 2 if self.names[index] == "ProcessID" else 1)

    def GetNames(self, index):  # noqa: N802
        return (self.names[index],)


class FakeDesigner:
    def __init__(self, parts, events):
        self.parts = parts
        self.events = events
        self.project_path = Path(os.environ["XPEDITION_CLI_CONFIG_DIR"]) / "display-fixture.prj"
        self.project_path.write_text("offline project fixture", encoding="utf-8")
        self.current_path = str(self.project_path.resolve())
        self.designs = ["Board1"]
        self.ProcessID = 4242
        self.opened = True
        self.failure = None
        self.on_reopen = None
        self.saved_parts = copy.deepcopy(parts)
        self._oleobj_ = SimpleNamespace(GetTypeInfo=FakeTypeInfo)
        self.Documents = SimpleNamespace(SaveAll=self.save_all, Open=self.forbidden)
        self.ActiveDocument = SimpleNamespace(Close=self.forbidden, Save=self.forbidden)
        self.ActiveView = SimpleNamespace(Block=SimpleNamespace(DeSelectAll=lambda: None))

    def forbidden(self, *args, **kwargs):
        pytest.fail("display apply must use project lifecycle, never sheet Save/Close/Open")

    def IsProjectOpened(self):  # noqa: N802
        return self.opened

    def GetProjectData(self):  # noqa: N802
        return SimpleNamespace(
            GetProjectFilePath=lambda: self.current_path if self.opened else "",
            GetiCDBDesigns=lambda: SimpleNamespace(
                GetCount=lambda: len(self.designs),
                GetItem=lambda index: self.designs[index - 1],
            ),
        )

    def GetActiveDesign(self):  # noqa: N802
        return "Board1"

    def save_all(self):
        self.events.append("SaveAll")
        if self.failure == "save":
            raise RuntimeError("fixture SaveAll failed")
        if self.failure != "save-ignored":
            self.saved_parts = copy.deepcopy(self.parts)

    def CloseProject(self):  # noqa: N802
        self.events.append("CloseProject")
        if self.failure == "close-raises":
            raise RuntimeError("fixture CloseProject failed")
        if self.failure == "close-false":
            return False
        if self.failure != "close-stays-open":
            self.opened = False
        return True

    def OpenProject(self, path):  # noqa: N802
        self.events.append("OpenProject")
        if self.failure == "open":
            raise RuntimeError("fixture OpenProject failed")
        assert not self.opened and self.saved_parts is not None
        self.parts[:] = copy.deepcopy(self.saved_parts)
        self.current_path = path
        self.opened = True
        if self.on_reopen:
            self.on_reopen(self)
        return True


def _apply(app, operations, **options):
    return native._apply_designer_display_changes(
        app,
        {"project": str(app.project_path), "operations": operations, **options},
        None,
        "Board1",
    )


def _harness(monkeypatch, parts, mutate=None):
    events = []
    snapshots = []
    app = FakeDesigner(parts, events)

    def snapshot(app, params):
        observed = _snapshot(parts)
        if snapshots and mutate:
            mutate(observed)
        snapshots.append(copy.deepcopy(observed))
        events.append("snapshot")
        return observed

    monkeypatch.setattr(native, "_designer_snapshot", snapshot)
    monkeypatch.setattr(
        native,
        "_designer_active_component",
        lambda app, params: next(part for part in parts if part.Refdes == params["refdes"]),
    )
    monkeypatch.setattr(native, "_ensure_sheet", lambda app, sheet: events.append(("sheet", sheet)))
    monkeypatch.setattr(native, "_PromptAnswerer", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(native, "_settle", lambda seconds: None)
    return app, events, snapshots


def test_native_attribute_uses_point_returning_getlocation_and_integer_visibility():
    attr = FakeAttr()
    attr.fields["Visible"] = 3
    actual = native._designer_attribute_display(attr, _owner())
    assert actual == {
        "owner": _owner(),
        "name": "Value",
        "value": "4.7k",
        "display": {
            "x": 120,
            "y": 240,
            "visible": 3,
            "size": 10,
            "orientation": 0,
            "origin": 1,
            "units": "sheet_unit",
        },
        "getter_errors": {},
    }
    assert attr.location_reads == 1 and attr.writes == []


def test_native_attribute_also_reads_getlocation_point_property():
    class PointProperty(FakeAttr):
        @property
        def GetLocation(self):  # noqa: N802
            return self.point

    record = native._designer_attribute_display(PointProperty(), _owner())
    assert record["display"]["x"] == 120 and record["display"]["y"] == 240
    assert record["getter_errors"] == {}


@pytest.mark.parametrize(
    ("member", "key"),
    [
        ("Visible", "visible"),
        ("Size", "size"),
        ("Orientation", "orientation"),
        ("Origin", "origin"),
    ],
)
@pytest.mark.parametrize("fault", ["raises", "missing", "bool", "float", "none"])
def test_missing_or_invalid_native_display_getter_is_reported_never_defaulted(member, key, fault):
    attr = FakeAttr()
    if fault == "raises":
        attr.fail.add(member)
    elif fault == "missing":
        del attr.fields[member]
    else:
        attr.fields[member] = {"bool": True, "float": 0.0, "none": None}[fault]
    record = native._designer_attribute_display(attr, _owner())
    assert key not in record["display"]
    assert member in record["getter_errors"]
    assert attr.writes == []


@pytest.mark.parametrize("fault", ["raises", "missing-x", "float-x", "bool-y", "none"])
def test_failed_location_never_synthesizes_zero_coordinates(fault):
    attr = FakeAttr()
    if fault == "raises":
        attr.fail.add("GetLocation")
    elif fault == "missing-x":
        del attr.point.X
    elif fault == "float-x":
        attr.point.X = 0.0
    elif fault == "bool-y":
        attr.point.Y = False
    else:
        attr.point = None
    record = native._designer_attribute_display(attr, _owner())
    assert not {"x", "y"} & record["display"].keys()
    assert "GetLocation" in record["getter_errors"]


def test_only_finite_named_attribute_families_have_display_getters_read():
    part = _parts()[2]
    part.Attributes = Collection([FakeAttr(name) for name in DISPLAY_NAMES])
    custom = FakeAttr("Custom Free Text", "preserved value")
    custom.fail.update({"GetLocation", "Visible", "Size", "Orientation", "Origin"})
    part.Attributes.append(custom)
    attributes, displays, coverage, pairs = native._designer_attributes(part, _owner())
    assert attributes["Custom Free Text"] == "preserved value"
    assert [row["name"] for row in displays] == list(DISPLAY_NAMES)
    assert len(pairs) == 6 and coverage["enumeration_complete"] is True
    assert coverage["other_text_graphics_observed"] is False
    assert coverage["exact_font_envelope_observed"] is False
    assert custom.location_reads == 0
    assert coverage["sheet_unit_mils"] == 10


def test_attribute_collection_is_bounded_and_marks_truncation():
    part = _parts()[2]
    part.Attributes = Collection([FakeAttr(f"Extra {index}") for index in range(257)])
    attributes, displays, coverage, pairs = native._designer_attributes(part, _owner())
    assert len(attributes) == 256 and displays == pairs == []
    assert coverage["attribute_count"] == 257 and coverage["attribute_limit"] == 256
    assert coverage["enumeration_complete"] is False and coverage["truncated"] is True


@pytest.mark.parametrize("name", ["Value", "Part Label", "PartLabel"])
@pytest.mark.parametrize("size", [None, 5, 30])
def test_native_display_success_reads_exact_fields_and_verifies_one_save_close_reopen(
    monkeypatch, name, size
):
    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    operation = _operation(name, size=size)
    steps = native_operations(_snapshot(parts), [operation])
    result = _apply(app, steps)
    target = parts[2].Attributes[0 if name == "Value" else 1]
    expected_writes = [("Value", "RC label"), ("SetLocation", 233, 377), ("Visible", VALUE_VISIBLE)]
    if size is not None:
        expected_writes.append(("Size", size))
    assert target.writes == expected_writes == _all_writes(parts)
    assert result["applied"][0]["added"] is False
    assert result["verification"]["valid"] is result["verification"]["complete"] is True
    assert result["saved"] is result["save_acknowledged"] is True
    assert result["persistence_verified"] is True
    assert events == [
        "snapshot",
        ("sheet", 1),
        "snapshot",
        "SaveAll",
        "CloseProject",
        "OpenProject",
        "snapshot",
    ]
    assert len(snapshots) == 3 and result["read_back_before_save"] == snapshots[1]
    assert result["read_back_after_reopen"] == snapshots[-1]
    lifecycle = result["lifecycle"]
    assert lifecycle["source"] == "designer_save_close_reopen"
    assert [lifecycle[key] for key in ("save_count", "close_count", "reopen_count")] == [1, 1, 1]
    assert lifecycle["save_acknowledged"] is lifecycle["closed_observed"] is True
    assert lifecycle["close_returned"] is True
    assert lifecycle["before_pid"] == lifecycle["reopened_pid"] == 4242
    assert lifecycle["before_identity"] == lifecycle["reopened_identity"]
    assert lifecycle["before_identity"]["path"] == str(app.project_path.resolve())
    assert target.Origin == 1 and target.Orientation == 0
    assert target.Size == (10 if size is None else size)


@pytest.mark.parametrize(
    "fault",
    [
        "missing-target",
        "duplicate-target",
        "orientation",
        "size",
        "origin",
        "visible",
        "location",
        "value",
        "uid",
    ],
)
def test_native_display_preflight_refuses_unobserved_or_ambiguous_target_with_zero_writes(
    monkeypatch, fault
):
    parts = _parts()
    target = parts[2].Attributes[1]
    if fault == "missing-target":
        parts[2].Attributes.remove(target)
    elif fault == "duplicate-target":
        parts[2].Attributes.append(FakeAttr("PartLabel", "duplicate"))
    elif fault == "orientation":
        target.fields["Orientation"] = 90
    elif fault == "size":
        del target.fields["Size"]
    elif fault == "origin":
        target.fail.add("Origin")
    elif fault == "visible":
        target.fail.add("Visible")
    elif fault == "location":
        target.fail.add("GetLocation")
    elif fault == "value":
        target.fields["Value"] = None
    else:
        parts[2].UID = ""
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["write_attempted"] is False
    assert caught.value.details["saved"] is False
    assert _all_writes(parts) == [] and events == ["snapshot"]


def test_later_target_preflight_failure_leaves_entire_native_batch_unwritten(monkeypatch):
    parts = _parts()
    parts[7].Attributes[0].fail.add("Size")
    operations = [_operation(), {**_operation("Value"), "refdes": "C2"}]
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, operations)
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["write_attempted"] is False
    assert _all_writes(parts) == [] and events == ["snapshot"]


def test_native_display_preview_record_conflict_is_refused_before_any_write(monkeypatch):
    parts = _parts()
    steps = native_operations(_snapshot(parts), [_operation()])
    parts[2].Attributes[1].point.X += 1
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, steps)
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["write_attempted"] is False
    assert _all_writes(parts) == [] and events == ["snapshot"]


@pytest.mark.parametrize("ignored", ["Value", "SetLocation", "Visible", "Size"])
def test_ignored_native_setter_fails_readback_and_cannot_save(monkeypatch, ignored):
    parts = _parts()
    parts[2].Attributes[1].ignore.add(ignored)
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation(size=15)])
    details = caught.value.details
    assert caught.value.code == "E_PROJECT_INVALID" and details["stage"] == "display_read_back"
    assert details["write_attempted"] is True
    assert details["saved"] is details["save_acknowledged"] is False
    assert details["persistence_verified"] is False and "observed" in details
    assert "SaveAll" not in events and _all_writes(parts)


@pytest.mark.parametrize(
    "member", ["Value", "GetLocation", "Visible", "Size", "Origin", "Orientation"]
)
def test_getter_lost_after_native_setter_stays_incomplete_and_cannot_save(monkeypatch, member):
    parts = _parts()
    parts[2].Attributes[1].fail_after_write.add(member)
    app, events, _ = _harness(monkeypatch, parts)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    assert caught.value.code == "E_PROJECT_INVALID"
    assert member in caught.value.details["observed"]["getter_errors"]
    assert caught.value.details["saved"] is caught.value.details["save_acknowledged"] is False
    assert "SaveAll" not in events


@pytest.mark.parametrize("index", range(8))
@pytest.mark.parametrize("field", ["uid", "attributes", "display", "pin-net"])
def test_every_component_identity_attributes_displays_and_pin_nets_are_protected_before_save(
    monkeypatch, index, field
):
    def drift(snapshot):
        component = snapshot["components"][index]
        if field == "uid":
            component["uid"] = "replaced-native-instance"
        elif field == "attributes":
            component["attributes"]["Unexpected"] = "changed"
        elif field == "display":
            component["attribute_display"][2]["display"]["x"] += 1
        else:
            component["pins"][1]["net"] = "SIGNAL"

    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts, drift)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    details = caught.value.details
    assert caught.value.code == "E_PROJECT_INVALID"
    assert details["stage"] == "before_save_verification"
    assert details["verification"]["valid"] is False
    assert details["saved"] is details["save_acknowledged"] is False
    assert details["observed"] == snapshots[-1]
    assert _all_writes(parts) and "SaveAll" not in events


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: s["connections"][0]["pins"].pop(), id="connection-pin"),
        pytest.param(lambda s: s["nets"].pop(), id="net"),
        pytest.param(lambda s: s["sheets"].pop(), id="sheet"),
        pytest.param(lambda s: s["unnamed_components"].append({"uid": "orphan"}), id="unnamed"),
        pytest.param(lambda s: s["components"].pop(), id="component-removed"),
        pytest.param(lambda s: s["components"][7].pop("pins"), id="pin-getters-lost"),
        pytest.param(lambda s: s.pop("connections"), id="protection-collection-lost"),
    ],
)
def test_native_display_protection_collection_drift_or_loss_prevents_save(monkeypatch, mutate):
    parts = _parts()
    app, events, _ = _harness(monkeypatch, parts, mutate)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    assert caught.value.details["verification"]["valid"] is False
    assert caught.value.details["saved"] is False and "SaveAll" not in events


@pytest.mark.parametrize(
    ("failure", "counts", "acknowledged", "saved"),
    [
        ("save", [1, 0, 0], False, None),
        ("close-raises", [1, 1, 0], True, True),
        ("close-false", [1, 1, 0], True, True),
        ("close-stays-open", [1, 1, 0], True, True),
        ("open", [1, 1, 1], True, True),
    ],
)
def test_native_lifecycle_failure_keeps_save_fact_and_never_replays_or_saves_in_finally(
    monkeypatch, failure, counts, acknowledged, saved
):
    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    app.failure = failure
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    details = caught.value.details
    lifecycle = details["lifecycle"]
    assert [lifecycle[key] for key in ("save_count", "close_count", "reopen_count")] == counts
    assert [events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject")] == counts
    assert lifecycle["save_acknowledged"] is details["save_acknowledged"] is acknowledged
    assert details["saved"] is saved
    assert details["persistence_verified"] is False and details["write_attempted"] is True
    assert details["observed"] == snapshots[-1] and len(snapshots) == 2
    assert _all_writes(parts) == [
        ("Value", "RC label"),
        ("SetLocation", 233, 377),
        ("Visible", VALUE_VISIBLE),
    ]
    assert "read_back_after_reopen" not in details


@pytest.mark.parametrize(
    "fault",
    ["pid-missing", "pid-zero", "pid-bool", "wrong-path", "no-designs", "closed", "no-save"],
)
def test_display_requires_actual_complete_project_identity_before_any_write(monkeypatch, fault):
    parts = _parts()
    app, events, _ = _harness(monkeypatch, parts)
    if fault == "pid-missing":
        app.ProcessID = None
    elif fault == "pid-zero":
        app.ProcessID = 0
    elif fault == "pid-bool":
        app.ProcessID = True
    elif fault == "wrong-path":
        app.current_path = str(app.project_path.with_name("other.prj"))
    elif fault == "no-designs":
        app.designs = []
    elif fault == "closed":
        app.opened = False
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()], **({"save": False} if fault == "no-save" else {}))
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["write_attempted"] is False
    assert _all_writes(parts) == [] and events == ["snapshot"]


@pytest.mark.parametrize("fault", ["pid", "path", "designs", "closed"])
def test_reopened_identity_drift_retains_acknowledged_save_without_persistence(monkeypatch, fault):
    def on_reopen(app):
        if fault == "pid":
            app.ProcessID += 1
        elif fault == "path":
            app.current_path = str(app.project_path.with_name("other.prj"))
        elif fault == "designs":
            app.designs.append("UnexpectedDesign")
        else:
            app.opened = False

    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    app.on_reopen = on_reopen
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    details = caught.value.details
    assert caught.value.code == "E_CONFLICT"
    assert details["save_acknowledged"] is details["saved"] is True
    assert details["persistence_verified"] is False
    assert details["lifecycle"]["reopened_identity"] != details["lifecycle"]["before_identity"] or (
        details["lifecycle"]["reopened_pid"] != details["lifecycle"]["before_pid"]
    )
    assert [events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject")] == [1, 1, 1]
    assert len(snapshots) == 2 and "read_back_after_reopen" not in details


@pytest.mark.parametrize(
    "member", ["Value", "GetLocation", "Visible", "Size", "Origin", "Orientation"]
)
def test_reopened_target_getter_loss_is_observed_and_never_claims_persistence(monkeypatch, member):
    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    app.on_reopen = lambda app: app.parts[2].Attributes[1].fail.add(member)
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    details = caught.value.details
    assert caught.value.code == "E_PROJECT_INVALID"
    assert details["stage"] == "after_reopen_verification"
    assert details["verification"]["complete"] is details["verification"]["valid"] is False
    assert details["save_acknowledged"] is details["saved"] is True
    assert details["persistence_verified"] is False
    assert details["read_back_after_reopen"] == snapshots[-1] and len(snapshots) == 3
    row = details["read_back_after_reopen"]["components"][2]["attribute_display"][1]
    assert member in row["getter_errors"]
    assert [events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject")] == [1, 1, 1]


@pytest.mark.parametrize(
    "fault",
    [
        "target-position",
        "target-value",
        "target-visible",
        "target-size",
        "target-origin",
        "target-orientation",
        "other-uid",
        "other-attributes",
        "other-display",
        "other-pin-net",
    ],
)
def test_reopened_requested_display_or_protected_record_drift_is_independently_rejected(
    monkeypatch, fault
):
    def on_reopen(app):
        target = app.parts[2].Attributes[1]
        other = app.parts[7]
        if fault == "target-position":
            target.point.X += 1
        elif fault.startswith("target-"):
            member = fault.removeprefix("target-").capitalize()
            target.fields[member] = "wrong" if member == "Value" else target.fields[member] + 1
        elif fault == "other-uid":
            other.UID = "$2I999"
        elif fault == "other-attributes":
            other.Attributes.append(FakeAttr("Unexpected", "changed"))
        elif fault == "other-display":
            other.Attributes[2].point.Y += 1
        else:
            other.connections[1].Net.LogicalNetName = "SIGNAL"

    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    app.on_reopen = on_reopen
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    details = caught.value.details
    assert caught.value.code == "E_PROJECT_INVALID"
    assert details["stage"] == "after_reopen_verification"
    assert details["verification"]["valid"] is False
    assert details["save_acknowledged"] is details["saved"] is True
    assert details["persistence_verified"] is False
    assert details["read_back_after_reopen"] == snapshots[-1]
    assert details["read_back_after_reopen"] != details["observed"]
    assert [events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject")] == [1, 1, 1]


def test_acknowledged_but_ignored_save_is_detected_from_reopened_saved_state(monkeypatch):
    parts = _parts()
    app, events, snapshots = _harness(monkeypatch, parts)
    app.failure = "save-ignored"
    with pytest.raises(native.AdapterError) as caught:
        _apply(app, [_operation()])
    details = caught.value.details
    assert details["stage"] == "after_reopen_verification"
    assert details["save_acknowledged"] is True and details["persistence_verified"] is False
    target = details["read_back_after_reopen"]["components"][2]["attribute_display"][1]
    assert target == snapshots[0]["components"][2]["attribute_display"][1]
    assert target != details["observed"]["components"][2]["attribute_display"][1]
    assert [events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject")] == [1, 1, 1]


@pytest.mark.parametrize(
    "display",
    [
        None,
        {},
        {"x": 1, "y": 2},
        {"x": True, "y": 2, "visible": True},
        {"x": 1.0, "y": 2, "visible": True},
        {"x": 1, "y": "2", "visible": True},
        {"x": 2**31, "y": 2, "visible": True},
        {"x": -(2**31) - 1, "y": 2, "visible": True},
        {"x": 1, "y": 2, "visible": 3},
        {"x": 1, "y": 2, "visible": False},
        {"x": 1, "y": 2, "visible": True, "size": 4},
        {"x": 1, "y": 2, "visible": True, "size": 31},
        {"x": 1, "y": 2, "visible": True, "size": True},
        {"x": 1, "y": 2, "visible": True, "size": 10.0},
        {"x": 1, "y": 2, "visible": True, "orientation": 0},
    ],
)
def test_changeset_rejects_bad_display_shape_without_mutating_input(display):
    design = _snapshot(_parts())
    before = copy.deepcopy(design)
    with pytest.raises(CLIError) as caught:
        apply_operations(design, [{**_operation(), "display": display}])
    assert caught.value.code == "E_VALIDATION" and design == before


@pytest.mark.parametrize(
    "name", ["Name", "Part Name", "Part Number", "Ref Designator", "Tolerance"]
)
def test_display_changeset_only_accepts_value_or_part_label(name):
    with pytest.raises(CLIError) as caught:
        apply_operations(_snapshot(_parts()), [_operation(name)])
    assert caught.value.code == "E_VALIDATION"


def test_changeset_projects_display_and_carries_exact_observed_record_to_native():
    design = _snapshot(_parts())
    operation = _operation(size=15)
    projected, changes = apply_operations(design, [operation])
    before = design["components"][2]["attribute_display"][1]
    after = projected["components"][2]["attribute_display"][1]
    assert after["display"] == {**before["display"], "x": 233, "y": 377, "visible": 3, "size": 15}
    assert projected["components"][2]["attributes"]["Part Label"] == "RC label"
    assert projected["components"][2]["value"] == design["components"][2]["value"]
    assert changes == [
        {
            "action": "set_property",
            "resource": "component",
            "id": "R1",
            "before": before,
            "after": after,
        }
    ]
    steps = native_operations(design, [operation])
    assert steps[0]["sheet"] == 1 and steps[0]["display_before"] == before
    assert "sheet" not in operation and "display_before" not in operation


def test_display_batch_refuses_mixed_non_display_operations_and_wrong_sheet():
    design = _snapshot(_parts())
    with pytest.raises(CLIError) as caught:
        apply_operations(
            design,
            [
                _operation(),
                {"type": "set_property", "refdes": "R2", "name": "Tolerance", "value": "1%"},
            ],
        )
    assert caught.value.code == "E_VALIDATION"
    with pytest.raises(CLIError) as caught:
        apply_operations(design, [{**_operation(), "sheet": 2}])
    assert caught.value.code == "E_VALIDATION"


def test_cli_display_preview_keeps_native_display_evidence_and_does_not_apply(
    cli, adapter, project, tmp_path
):
    design = _snapshot(_parts())
    adapter.on("snapshot", design)
    path = tmp_path / "display.json"
    path.write_text(json.dumps({"operations": [_operation(size=15)]}), encoding="utf-8")
    code, payload = cli(
        "schematic", "edit", "--project", str(project), "--file", str(path), "--dry-run"
    )
    assert code == 0 and payload["ok"] is True
    preview = payload["data"]["preview"]
    step = preview["steps"][0]
    assert step["sheet"] == 1
    assert step["display_before"] == design["components"][2]["attribute_display"][1]
    assert step["display"] == {"x": 233, "y": 377, "visible": True, "size": 15}
    assert preview["changes"][0]["before"] == step["display_before"]
    assert preview["changes"][0]["after"]["display"]["visible"] == 3
    assert "apply_changeset" not in adapter.methods()


def test_cli_rejects_missing_display_target_before_native_apply(cli, adapter, project, tmp_path):
    design = _snapshot(_parts())
    design["components"][2]["attribute_display"].pop(1)
    adapter.on("snapshot", design)
    path = tmp_path / "display.json"
    path.write_text(json.dumps({"operations": [_operation()]}), encoding="utf-8")
    code, payload = cli(
        "schematic", "edit", "--project", str(project), "--file", str(path), "--dry-run"
    )
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
    assert "apply_changeset" not in adapter.methods()
