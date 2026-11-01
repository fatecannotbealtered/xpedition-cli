"""Legacy projection, sheet selection, token and requested-postcondition tests.

These private-handler mocks are not current protected-native/FCC evidence.
Their test-only eligibility replacement preserves historical operation and
confirmation algorithm coverage; separate complete-observation preflight,
durability and real SDK receipt tests use the production validator unchanged.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
from types import SimpleNamespace

import pytest
from fakes import Failure, FakeAdapter
from fakes import schematic_snapshot as base_schematic_snapshot

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli.changeset import apply_operations
from xpedition_cli.cli.schematic import native_operations
from xpedition_cli.errors import CLIError


def schematic_snapshot():
    """These legacy fake parts have explicitly empty native attribute records."""
    snapshot = base_schematic_snapshot()
    snapshot["unnamed_components"] = []
    for component in snapshot["components"]:
        component["attributes"] = {}
    return snapshot


@pytest.fixture(autouse=True)
def known_legacy_native_metadata(monkeypatch):
    from xpedition_cli import schematic_edit_protection

    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Legacy edit regressions cannot activate CAD/COM or native APIs")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden)
    monkeypatch.setattr(adapter, "_import_com", forbidden)
    monkeypatch.setattr(
        schematic_edit_protection,
        "validate_supported",
        lambda *args, **kwargs: {"valid": True, "complete": True, "fixture_only": True},
    )
    original = FakeAdapter._snapshot
    monkeypatch.setattr(
        FakeAdapter,
        "_snapshot",
        staticmethod(
            lambda params: (
                schematic_snapshot() if params.get("domain") == "schematic" else original(params)
            )
        ),
    )
    yield
    assert hits == []


def _design() -> dict:
    # U1, C1 on sheet 1, R1 and R2 on sheet 2; I2C_SDA joins R1.2 and U1.3 only
    return schematic_snapshot()


def test_deleting_a_part_keeps_the_rest_of_its_nets() -> None:
    projected, changes = apply_operations(_design(), [{"type": "delete_component", "refdes": "R1"}])
    assert "R1" not in {c["refdes"] for c in projected["components"]}
    plus = next(c for c in projected["connections"] if c["net"] == "+3V3")
    assert plus["pins"] == ["C1.1", "R2.1", "U1.1"]
    # a net left with one pin is no connection any more
    assert all(c["net"] != "I2C_SDA" for c in projected["connections"])
    assert changes[0]["action"] == "delete_component"


def test_a_property_is_projected_among_the_attributes_the_design_reads_back() -> None:
    op = {"type": "set_property", "refdes": "C1", "name": "Tolerance", "value": "10%"}
    projected, changes = apply_operations(_design(), [op])
    c1 = next(c for c in projected["components"] if c["refdes"] == "C1")
    assert c1["attributes"]["Tolerance"] == "10%"
    assert changes[0]["after"] == {"Tolerance": "10%"}
    for name in ("Ref Designator", "refdes", "A=B", ""):
        with pytest.raises(CLIError) as caught:
            apply_operations(_design(), [{**op, "name": name}])
        assert caught.value.code == "E_VALIDATION"
    with pytest.raises(CLIError):
        apply_operations(_design(), [{**op, "value": "two\nlines"}])


def test_a_disconnected_pin_leaves_its_net() -> None:
    projected, changes = apply_operations(_design(), [{"type": "disconnect", "pin": "R2.2"}])
    r2 = next(c for c in projected["components"] if c["refdes"] == "R2")
    assert [p["net"] for p in r2["pins"] if p["number"] == "2"] == [None]
    assert all("R2.2" not in c["pins"] for c in projected["connections"])
    assert changes[0]["before"] == {"net": "I2C_SCL"}
    with pytest.raises(CLIError) as caught:
        apply_operations(projected, [{"type": "disconnect", "pin": "R2.2"}])
    assert caught.value.code == "E_CONFLICT"
    with pytest.raises(CLIError) as caught:
        apply_operations(_design(), [{"type": "disconnect", "pin": "R2.9"}])
    assert caught.value.code == "E_NOT_FOUND"


def test_a_labelled_net_is_renamed_but_not_a_power_net_nor_onto_another_net() -> None:
    op = {"type": "rename_net", "net": "I2C_SCL", "name": "SCL_A"}
    projected, _ = apply_operations(_design(), [op])
    assert "SCL_A" in {n["name"] for n in projected["nets"]}
    assert {"R2.2", "U1.4"} <= set(
        next(c for c in projected["connections"] if c["net"] == "SCL_A")["pins"]
    )
    for old in ("+3V3", "GND"):
        with pytest.raises(CLIError) as caught:
            apply_operations(_design(), [{**op, "net": old}])
        assert caught.value.code == "E_VALIDATION" and "power or ground" in caught.value.message
    with pytest.raises(CLIError) as caught:
        apply_operations(_design(), [{**op, "name": "I2C_SDA"}])
    assert caught.value.code == "E_CONFLICT" and "merge" in caught.value.message
    with pytest.raises(CLIError):
        apply_operations(_design(), [{**op, "name": "TWO WORDS"}])


def test_operations_go_to_their_part_sheet_and_a_rename_to_every_sheet() -> None:
    steps = native_operations(
        _design(),
        [
            {"type": "delete_component", "refdes": "R1"},
            {"type": "disconnect", "pin": "U1.4"},
            {"type": "set_property", "refdes": "C1", "name": "X", "value": "1", "sheet": 9},
            {"type": "rename_net", "net": "I2C_SDA", "name": "SDA_A"},
        ],
    )
    assert steps[0]["sheet"] == 2 and steps[1]["sheet"] == 1 and steps[2]["sheet"] == 9
    assert [s["sheet"] for s in steps[3:]] == [1, 2]
    assert all(s["type"] == "rename_net" for s in steps[3:])


def test_the_edit_command_sends_the_sheet_by_sheet_steps(cli, adapter, project, tmp_path) -> None:
    changes = tmp_path / "changes.json"
    changes.write_text(
        json.dumps({"operations": [{"type": "rename_net", "net": "I2C_SDA", "name": "SDA_A"}]}),
        encoding="utf-8",
    )
    renamed = schematic_snapshot()
    for component in renamed["components"]:
        for pin in component["pins"]:
            if pin["net"] == "I2C_SDA":
                pin["net"] = "SDA_A"
    for net in renamed["nets"]:
        if net["name"] == "I2C_SDA":
            net["name"] = "SDA_A"
    for connection in renamed["connections"]:
        if connection["net"] == "I2C_SDA":
            connection["net"] = "SDA_A"
    reads = iter([schematic_snapshot(), schematic_snapshot(), renamed])
    adapter.on("snapshot", lambda params: next(reads))
    adapter.on("apply_changeset", {"applied": [], "saved": True})
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    code, payload = cli(*args, "--dry-run")
    assert code == 0 and [s["sheet"] for s in payload["data"]["preview"]["steps"]] == [1, 2]
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["verification"]["valid"] is True
    sent = adapter.last("apply_changeset")["operations"]
    assert [(s["type"], s["sheet"]) for s in sent] == [("rename_net", 1), ("rename_net", 2)]
    assert "SDA_A" in {n["name"] for n in payload["data"]["observed"]["nets"]}
    assert payload["data"]["persistence_verified"] is False


def _move_args(project, tmp_path):
    changes = tmp_path / "move.json"
    changes.write_text(
        json.dumps(
            {"operations": [{"type": "move_component", "refdes": "U1", "x": 404, "y": 505}]}
        ),
        encoding="utf-8",
    )
    return ["schematic", "edit", "--project", str(project), "--file", str(changes)]


def test_success_keeps_post_snapshot_values_not_projected_or_applied_echo(
    cli, adapter, project, tmp_path
):
    observed = schematic_snapshot()
    part = observed["components"][0]
    part.update(x=404 + 2e-7, y=505 - 1e-7)
    part["attributes"] = {"GetterMarker": "only in the independent read"}
    observed["metadata"]["source"] = "simulated Designer snapshot"
    reads = iter([schematic_snapshot(), schematic_snapshot(), observed])
    adapter.on("snapshot", lambda params: next(reads))
    adapter.on("apply_changeset", {"applied": [{"x": 404, "y": 505}], "saved": True})
    args = _move_args(project, tmp_path)
    code, preview = cli(*args, "--dry-run")
    assert code == 0
    code, payload = cli(*args, "--confirm", preview["data"]["confirm_token"])
    assert code == 0
    data = payload["data"]
    actual = next(c for c in data["observed"]["components"] if c["refdes"] == "U1")
    assert actual["x"] == part["x"] != data["applied"][0]["x"]
    assert actual["y"] == part["y"] != data["applied"][0]["y"]
    assert actual["attributes"]["GetterMarker"] == "only in the independent read"
    canonical = json.dumps(
        data["observed"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert data["read_back"]["snapshot_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert data["read_back"]["source"]["application"] == "simulated Designer snapshot"
    assert data["read_back"]["source"]["application_version"] is None
    assert data["persistence_verified"] is False and "observed" in data["_untrusted"]


def test_ignored_edit_retains_actual_failure_snapshot_and_saved_fact(
    cli, adapter, project, tmp_path
):
    adapter.on("apply_changeset", {"applied": [{"x": 404, "y": 505}], "saved": True})
    args = _move_args(project, tmp_path)
    _, preview = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and payload["error"]["code"] == "E_PROJECT_INVALID"
    details = payload["error"]["details"]
    assert details["saved"] is True and details["write_attempted"] is True
    assert details["observed"]["components"][0]["x"] == 400
    assert details["verification"]["valid"] is False
    assert details["persistence_verified"] is False


def test_failed_readback_does_not_synthesize_observed_from_the_write(
    cli, adapter, project, tmp_path
):
    reads = iter([schematic_snapshot(), schematic_snapshot()])

    def snapshot(params):
        try:
            return next(reads)
        except StopIteration:
            raise Failure("E_TIMEOUT", "simulated read timeout") from None

    adapter.on("snapshot", snapshot)
    adapter.on("apply_changeset", {"applied": [{"x": 404, "y": 505}], "saved": True})
    args = _move_args(project, tmp_path)
    _, preview = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2
    details = payload["error"]["details"]
    assert details["stage"] == "read_back" and details["saved"] is True
    assert "observed" not in details and details["persistence_verified"] is False


# ---- the adapter, against stand-ins for Designer's objects ----


class Attr(SimpleNamespace):
    pass


class Collection(list):
    """A COM collection as the adapter walks it: Count, and Item from 1."""

    @property
    def Count(self) -> int:  # noqa: N802 - COM property name
        return len(self)

    def Item(self, index: int):  # noqa: N802 - COM method name
        return self[index - 1]


class Component:
    def __init__(self, refdes: str, attributes: dict | None = None) -> None:
        self.Refdes = refdes
        self.attributes = {k: Attr(Name=k, Value=v) for k, v in (attributes or {}).items()}
        self.added: list[str] = []
        self.Selected = False

    def FindAttribute(self, name):  # noqa: N802 - COM method name
        return self.attributes.get(name)

    def AddAttribute(self, text, x, y, visibility):  # noqa: N802 - COM method name
        self.added.append((text, visibility))
        return Attr(Name=text.split("=")[0], Value=text.split("=")[1])

    @property
    def GetLocation(self):  # noqa: N802 - a property on current Designer
        return SimpleNamespace(X=100, Y=200)


class Block:
    def __init__(self) -> None:
        self.deleted: list[bool] = []
        self.deselected = 0

    def DeSelectAll(self):  # noqa: N802 - COM method name
        self.deselected += 1

    def DeleteSelected(self, unconnected):  # noqa: N802 - COM method name
        self.deleted.append(unconnected)


def _app(block: Block) -> SimpleNamespace:
    return SimpleNamespace(ActiveView=SimpleNamespace(Block=block))


def test_set_property_edits_an_attribute_or_adds_it_hidden(monkeypatch) -> None:
    part = Component("R1", {"Part Number": "RES-10K"})
    monkeypatch.setattr(adapter, "_designer_active_component", lambda app, params: part)
    block = Block()
    op = {"type": "set_property", "refdes": "R1", "name": "Part Number", "value": "RES-4K7"}
    result = adapter._apply_designer_operation(_app(block), op, None, "Board1", {})
    assert result["added"] is False and part.attributes["Part Number"].Value == "RES-4K7"
    op = {"type": "set_property", "refdes": "R1", "name": "Tolerance", "value": "1%"}
    result = adapter._apply_designer_operation(_app(block), op, None, "Board1", {})
    assert result["added"] is True and part.added == [("Tolerance=1%", 0)]
    assert block.deselected == 2  # nothing selected rides along


def test_delete_takes_the_wires_only_the_part_used_with_their_symbols(monkeypatch) -> None:
    part = Component("R1")
    ground_mark = Component("")
    shared, stub = object(), object()
    connections = Collection([SimpleNamespace(Net=shared), SimpleNamespace(Net=stub)])
    owners = {
        id(shared): [("R1", "1", part), ("C1", "1", Component("C1"))],
        id(stub): [("R1", "2", part), ("", "1", ground_mark)],
    }
    cut: list[object] = []
    monkeypatch.setattr(adapter, "_designer_active_component", lambda app, params: part)
    monkeypatch.setattr(adapter, "_com_member", lambda obj, name: connections)
    monkeypatch.setattr(adapter, "_wire_owners", lambda net: owners[id(net)])

    def fake_cut(block, net, keep, stub, segment=None):
        cut.append(net)
        for refdes, _n, component in owners[id(net)]:
            if refdes == "":
                component.Selected = True
        return [(10, 20)], 1

    monkeypatch.setattr(adapter, "_cut_wire", fake_cut)
    monkeypatch.setattr(adapter, "_remove_label_boxes", lambda app, points: len(points))
    block = Block()
    op = {"type": "delete_component", "refdes": "R1"}
    result = adapter._apply_designer_operation(_app(block), op, None, "Board1", {})
    # the wire shared with C1 stays; the ground stub and its symbol go with the part
    assert cut == [stub] and ground_mark.Selected is True and part.Selected is True
    assert block.deleted == [True]
    assert result["wires_removed"] == 1 and result["symbols_removed"] == 1
    assert result["label_boxes_removed"] == 1


def test_rename_refuses_a_net_a_power_symbol_names(monkeypatch) -> None:
    label = SimpleNamespace(TextString="VBUS")
    net = SimpleNamespace(GetLabel=lambda segment: label)
    part = Component("J1")
    monkeypatch.setattr(
        adapter, "_designer_collection", lambda app, method, design: Collection([part])
    )

    def member(obj, name):
        if name == "GetConnections":
            return Collection([SimpleNamespace(Net=net)])
        if name == "GetSegments":
            return Collection([object()])
        return ""

    monkeypatch.setattr(adapter, "_com_member", member)
    monkeypatch.setattr(adapter, "_wire_owners", lambda wire: [("J1", "1", part), ("", "1", None)])
    op = {"type": "rename_net", "net": "VBUS", "name": "VIN"}
    with pytest.raises(adapter.AdapterError) as caught:
        adapter._apply_designer_operation(_app(Block()), op, None, "Board1", {})
    assert caught.value.code == "E_VALIDATION" and label.TextString == "VBUS"
    monkeypatch.setattr(adapter, "_wire_owners", lambda wire: [("J1", "1", part)])
    result = adapter._apply_designer_operation(_app(Block()), op, None, "Board1", {})
    assert label.TextString == "VIN" and result["labels"] == 1


def test_the_operations_schema_lists_the_new_types() -> None:
    from xpedition_cli.edit_operations import SUPPORTED, input_schema

    assert {"delete_component", "set_property", "disconnect", "rename_net"} <= set(SUPPORTED)
    kinds = {
        v["properties"]["type"]["const"]
        for v in input_schema()["properties"]["operations"]["items"]["oneOf"]
    }
    assert kinds == set(SUPPORTED)
    assert copy.deepcopy(input_schema())
