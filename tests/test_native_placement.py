from __future__ import annotations

import copy
import json

import pytest

from xpedition_cli.errors import CLIError
from xpedition_cli.native_placement import LayoutPlacementDriver
from xpedition_cli.placement import execute_placement, plan_placement


class Component:
    def __init__(self, name, x, *, side=1, anchor=0, fix_lock=0):
        self.RefDes, self.UniqueId = name, "id-" + name
        self.x, self.y, self.angle = x, 10.0, 0.0
        self.Side, self.Anchor, self.FixLock, self.Placed = side, anchor, fix_lock, True
        self.calls = []
        self.failure = None

    def GetPositionX(self, unit):
        assert unit == 4
        return self.x

    def GetPositionY(self, unit):
        assert unit == 4
        return self.y

    def GetOrientation(self, unit):
        assert unit == 0
        return self.angle

    def UnPlace(self):
        self.calls.append("unplace")
        self.Placed = False

    def Place(self, x, y, rotation, top, fix, unit, angle_unit):
        self.calls.append((x, y, rotation, top, fix, unit, angle_unit))
        assert unit == 4 and angle_unit == 0 and fix == 0
        if self.failure == "refused":
            raise RuntimeError("private upstream error")
        self.Placed = True
        if self.failure != "swallowed":
            self.x, self.y, self.angle = x, y, rotation
            self.Side = 1 if top else 512


class Collection:
    def __init__(self, rows):
        self.rows = rows
        self.Count = len(rows)
        self.reads = 0

    def Item(self, index):
        self.reads += 1
        return self.rows[index - 1]

    def __call__(self, *args):
        raise AssertionError("a COM collection must not be treated as a zero-argument function")


class Document:
    def __init__(self):
        self.rows = [Component("R1", 1), Component("R2", 20, side=512), Component("U9", 90)]
        self.Components = Collection(self.rows)
        self.RespectComponentPlacementDRC = False
        self.saved = 0

    def Save(self):
        self.saved += 1

    @property
    def Traces(self):
        raise AssertionError("batch must not access/delete routing")


def request():
    return {"schema_version": "1.0", "unit": "mm", "selection": ["R1", "R2"],
            "steps": [{"op": "translate", "dx": 2, "dy": 3}]}


def test_real_binding_uses_explicit_units_and_preserves_bottom_side_and_unselected():
    doc = Document()
    driver = LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)
    before_unselected = copy.deepcopy(vars(doc.rows[2]))
    plan = plan_placement(request(), driver.observe(request()["selection"]))
    assert not any(part.calls for part in doc.rows), "preview performed a placement"
    result = execute_placement(request(), plan["state_digest"], driver)
    assert result["outcome"] == "complete" and result["verification"]["valid"]
    assert doc.rows[0].calls[-1][3] is True
    assert doc.rows[1].calls[-1][3] is False
    assert vars(doc.rows[2]) == before_unselected
    assert doc.Components.reads == 3, "components were rescanned for each move"
    assert doc.saved == 1 and doc.RespectComponentPlacementDRC is False


@pytest.mark.parametrize("protection,value", [("Anchor", 1), ("Anchor", 2), ("Anchor", 3), ("FixLock", 1)])
def test_actual_anchor_and_fixlock_are_not_overridden(protection, value):
    doc = Document()
    setattr(doc.rows[1], protection, value)
    driver = LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)
    with pytest.raises(CLIError, match="locked or fixed"):
        plan_placement(request(), driver.observe(request()["selection"]))
    assert not doc.saved and not any(part.calls for part in doc.rows)


@pytest.mark.parametrize("attribute,value", [("Side", 0), ("Side", True), ("Anchor", None),
                                            ("FixLock", "0"), ("UniqueId", None), ("Placed", False)])
def test_unknown_native_state_fails_closed(attribute, value):
    doc = Document()
    setattr(doc.rows[1], attribute, value)
    driver = LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)
    with pytest.raises(CLIError) as error:
        driver.observe(request()["selection"])
    assert error.value.code == "E_BACKEND_UNAVAILABLE"
    assert not doc.saved and not any(part.calls for part in doc.rows)


def test_duplicate_refs_do_not_pick_an_arbitrary_component():
    doc = Document()
    doc.rows[2].RefDes = "R1"
    with pytest.raises(CLIError, match="ambiguous"):
        LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)


@pytest.mark.parametrize("mode", ["refused", "swallowed"])
def test_binding_surfaces_refusal_or_silent_failure_without_saving(mode):
    doc = Document()
    doc.rows[1].failure = mode
    driver = LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)
    plan = plan_placement(request(), driver.observe(request()["selection"]))
    result = execute_placement(request(), plan["state_digest"], driver)
    assert result["outcome"] == "partial_failure" and not result["verification"]["valid"]
    assert result["results"][0]["ok"] and not result["results"][1]["ok"]
    assert not doc.saved and doc.RespectComponentPlacementDRC is False
    assert "private upstream" not in json.dumps(result)


def test_unavailable_drc_never_unplaces_a_part():
    doc = Document()
    doc.RespectComponentPlacementDRC = None
    driver = LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)
    result = execute_placement(request(), plan_placement(request(), driver.observe(request()["selection"]))["state_digest"], driver)
    assert result["outcome"] == "failed" and not result["write_attempted"]
    assert not any(part.calls for part in doc.rows) and not doc.saved


def test_adapter_dispatch_wires_preview_apply_and_structured_errors(tmp_path, monkeypatch):
    from xpedition_cli import native_com_adapter as bridge
    doc = Document()
    board = tmp_path / "test.pcb"
    board.write_text("fixture only", encoding="utf-8")
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    lifecycle = []

    class Com:
        def CoInitialize(self):
            lifecycle.append("init")

        def CoUninitialize(self):
            lifecycle.append("uninit")

    monkeypatch.setattr(bridge, "_import_com", lambda: (Com(), None))
    monkeypatch.setattr(bridge, "_layout_board_path", lambda params: board)
    def application(client, attach_only=False):
        assert attach_only is True, "do not start or take ownership of a new user session"
        return doc
    monkeypatch.setattr(bridge, "_application", application)
    monkeypatch.setattr(bridge, "_open_layout_document", lambda app, path: (doc, []))
    monkeypatch.setattr(bridge, "_licensed_document", lambda app: doc)
    assert bridge.UNIT_MM == 4  # Cross-checked published EPcbUnit enum, not a display unit.
    params = {"project": str(board), "request": request(), "apply": False}
    preview = bridge.dispatch("placement_batch", params)
    assert preview["pcb"] == str(board) and not doc.saved
    assert not any(part.calls for part in doc.rows)
    applied = bridge.dispatch("placement_batch", {**params, "apply": True, "state_digest": preview["state_digest"]})
    assert applied["saved"] and doc.saved == 1
    with pytest.raises(bridge.AdapterError) as error:
        bridge.dispatch("placement_batch", {**params, "apply": True, "state_digest": preview["state_digest"]})
    assert error.value.code == "E_CONFLICT" and error.value.details["write_attempted"] is False
    assert lifecycle == ["init", "uninit"] * 3
