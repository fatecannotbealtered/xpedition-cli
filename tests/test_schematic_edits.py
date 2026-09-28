"""schematic edit: delete a part, set a property, disconnect a pin, rename a net --
projected first, carried out on the part's own sheet, then read back."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fakes import schematic_snapshot

from xpedition_cli import native_com_adapter as adapter
from xpedition_cli.changeset import apply_operations
from xpedition_cli.cli.schematic import native_operations
from xpedition_cli.errors import CLIError


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
