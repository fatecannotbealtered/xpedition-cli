from __future__ import annotations

import copy
import json

import pytest
from test_placement import material_state

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
        self.FabricationLayerTexts = Collection([])
        self.RespectComponentPlacementDRC = False
        self.saved = 0
        self.FullName = str(self.default_board)
        self.ActiveDocument = self

    def Save(self):
        self.saved += 1
        return True

    def Close(self, saved):
        assert saved is True
        self.ActiveDocument = None
        return True

    @property
    def Traces(self):
        raise AssertionError("batch must not access/delete routing")


def request():
    return {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": ["R1", "R2"],
        "steps": [{"op": "translate", "dx": 2, "dy": 3}],
    }


@pytest.fixture(autouse=True)
def native_boundary(tmp_path, monkeypatch):
    from xpedition_cli import cell_refresh, native_identity, native_process
    from xpedition_cli import native_com_adapter as bridge

    monkeypatch.setattr(Document, "default_board", tmp_path / "board.pcb", raising=False)
    monkeypatch.setattr(bridge, "_settle", lambda seconds: None)
    monkeypatch.setattr(bridge, "_licensed_document", lambda app: app.ActiveDocument)
    monkeypatch.setattr(native_identity, "application_identity", lambda app, domain: {"pid": 91})

    def generation(domain, pid):
        assert domain == "pcb" and pid in {91, 92}
        return {
            "pid": pid,
            "executable": "D:/fixture/ExpeditionPCB.exe",
            "creation_time_100ns": str(133000000000000000 + pid),
        }

    monkeypatch.setattr(native_process, "process_identity", generation)

    def reopen(app, path):
        assert app.ActiveDocument is None
        app.ActiveDocument = app
        app.FullName = str(path)
        return app, []

    def capture(doc, adapter, *, strict=False):
        assert strict is True
        rows = []
        for comp in doc.rows:
            rows.append(
                {
                    "refdes": comp.RefDes,
                    "object_id": str(comp.UniqueId),
                    "x": comp.GetPositionX(4),
                    "y": comp.GetPositionY(4),
                    "rotation": comp.GetOrientation(0),
                    "side": "top" if comp.Side == 1 else "bottom",
                    "placed": comp.Placed,
                    "anchor": comp.Anchor,
                    "fix_lock": comp.FixLock,
                    "unit": "mm",
                }
            )
        return material_state(rows)

    monkeypatch.setattr(bridge, "_open_layout_document", reopen)
    monkeypatch.setattr(cell_refresh, "capture", capture)


def driver_for(doc):
    from pathlib import Path

    from xpedition_cli import native_com_adapter as bridge

    return LayoutPlacementDriver(
        doc, request()["selection"], unit_mm=4, app=doc, board=Path(doc.FullName), bridge=bridge
    )


def preview_for(driver):
    return plan_placement(
        request(), driver.observe(request()["selection"]), protection_state=driver.capture_state()
    )


def test_real_binding_uses_explicit_units_and_preserves_bottom_side_and_unselected():
    doc = Document()
    driver = driver_for(doc)
    before_unselected = copy.deepcopy(vars(doc.rows[2]))
    plan = preview_for(driver)
    assert not any(part.calls for part in doc.rows), "preview performed a placement"
    result = execute_placement(request(), plan["state_digest"], driver)
    assert result["outcome"] == "complete" and result["verification"]["valid"]
    assert doc.rows[0].calls[-1][3] is True
    assert doc.rows[1].calls[-1][3] is False
    assert vars(doc.rows[2]) == before_unselected
    assert doc.Components.reads == 6, "one identity scan before edits and one after reopen"
    assert doc.saved == 1 and doc.RespectComponentPlacementDRC is False


@pytest.mark.parametrize(
    "protection,value", [("Anchor", 1), ("Anchor", 2), ("Anchor", 3), ("FixLock", 1)]
)
def test_actual_anchor_and_fixlock_are_not_overridden(protection, value):
    doc = Document()
    setattr(doc.rows[1], protection, value)
    driver = LayoutPlacementDriver(doc, request()["selection"], unit_mm=4)
    with pytest.raises(CLIError, match="locked or fixed"):
        plan_placement(request(), driver.observe(request()["selection"]))
    assert not doc.saved and not any(part.calls for part in doc.rows)


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("Side", 0),
        ("Side", True),
        ("Anchor", None),
        ("FixLock", "0"),
        ("UniqueId", None),
        ("Placed", False),
    ],
)
def test_unknown_native_state_fails_closed(attribute, value):
    doc = Document()
    setattr(doc.rows[1], attribute, value)
    with pytest.raises(CLIError) as error:
        driver = driver_for(doc)
        driver.observe(request()["selection"])
    assert error.value.code in {"E_BACKEND_UNAVAILABLE", "E_CONFLICT"}
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
    driver = driver_for(doc)
    plan = preview_for(driver)
    result = execute_placement(request(), plan["state_digest"], driver)
    assert result["outcome"] == "partial_failure" and not result["verification"]["valid"]
    assert result["results"][0]["ok"] and not result["results"][1]["ok"]
    assert not doc.saved and doc.RespectComponentPlacementDRC is False
    assert "private upstream" not in json.dumps(result)
    if mode == "swallowed":
        # A successful Place call is not evidence that the requested coordinates took.
        failed = result["results"][1]
        assert failed["observed"]["x"] == 20 and failed["target"]["x"] == 22


def test_success_retains_getter_roundoff_instead_of_copying_the_target():
    doc = Document()
    part = doc.rows[0]
    original_place = part.Place

    def rounded_place(*args):
        original_place(*args)
        part.x += 2e-7
        part.y -= 1e-7

    part.Place = rounded_place
    driver = driver_for(doc)
    plan = preview_for(driver)
    result = execute_placement(request(), plan["state_digest"], driver)
    assert result["outcome"] == "complete" and result["verification"]["valid"]
    row = result["results"][0]
    assert row["observed"]["x"] == part.GetPositionX(4) != row["target"]["x"]
    assert row["observed"]["y"] == part.GetPositionY(4) != row["target"]["y"]


def test_unavailable_drc_never_unplaces_a_part():
    doc = Document()
    doc.RespectComponentPlacementDRC = None
    driver = driver_for(doc)
    result = execute_placement(
        request(),
        preview_for(driver)["state_digest"],
        driver,
    )
    assert result["outcome"] == "failed" and not result["write_attempted"]
    assert not any(part.calls for part in doc.rows) and not doc.saved


def test_adapter_dispatch_wires_preview_apply_and_structured_errors(tmp_path, monkeypatch):
    from xpedition_cli import native_com_adapter as bridge

    doc = Document()
    board = tmp_path / "test.pcb"
    board.write_text("fixture only", encoding="utf-8")
    doc.FullName = str(board)
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    lifecycle = []

    class Com:
        def CoInitialize(self):
            lifecycle.append("init")

        def CoUninitialize(self):
            lifecycle.append("uninit")

    monkeypatch.setattr(bridge, "_import_com", lambda: (Com(), None))
    monkeypatch.setattr(bridge, "_layout_board_path", lambda params: board)

    def application(client, attach_only=False, quiet=True):
        assert attach_only is True, "do not start or take ownership of a new user session"
        assert quiet is False, "batch precondition must not mutate GUI preferences"
        return doc

    monkeypatch.setattr(bridge, "_application", application)

    def open_document(app, path):
        doc.ActiveDocument = doc
        return doc, []

    monkeypatch.setattr(bridge, "_open_layout_document", open_document)
    monkeypatch.setattr(bridge, "_licensed_document", lambda app: doc)
    assert bridge.UNIT_MM == 4  # Cross-checked published EPcbUnit enum, not a display unit.
    params = {"project": str(board), "request": request(), "apply": False}
    preview = bridge.dispatch("placement_batch", params)
    assert preview["pcb"] == str(board) and not doc.saved
    assert not any(part.calls for part in doc.rows)
    applied = bridge.dispatch(
        "placement_batch",
        {
            **params,
            "apply": True,
            "state_digest": preview["state_digest"],
            "state_guard": preview["state_guard"],
        },
    )
    assert applied["saved"] and doc.saved == 1
    with pytest.raises(bridge.AdapterError) as error:
        bridge.dispatch(
            "placement_batch",
            {
                **params,
                "apply": True,
                "state_digest": preview["state_digest"],
                "state_guard": preview["state_guard"],
            },
        )
    assert error.value.code == "E_CONFLICT" and error.value.details["write_attempted"] is False
    assert lifecycle == ["init", "uninit"] * 3


@pytest.mark.parametrize("stage", ["save", "close"])
@pytest.mark.parametrize("reply", [False, None, "exception"])
def test_strict_lifecycle_stops_at_first_unacknowledged_native_call(stage, reply, monkeypatch):
    doc = Document()
    driver = driver_for(doc)
    plan = preview_for(driver)
    calls = []

    def save():
        calls.append("save")
        if stage == "save":
            if reply == "exception":
                raise RuntimeError("private exception")
            return reply
        return True

    def close(saved):
        calls.append("close")
        if reply == "exception":
            raise RuntimeError("private exception")
        doc.ActiveDocument = None
        return reply

    monkeypatch.setattr(doc, "Save", save)
    monkeypatch.setattr(doc, "Close", close)
    report = execute_placement(request(), plan["state_digest"], driver)
    assert report["outcome"] == "persistence_incomplete"
    assert report["persistence_verified"] is False
    assert report["verification"]["valid"] is False
    assert report["lifecycle"]["open_count"] == 0
    assert calls == (["save"] if stage == "save" else ["save", "close"])
    assert "private exception" not in json.dumps(report)


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_board",
        "wrong_pid",
        "active_after_close",
        "missing_open_reply",
        "stale_component_identity",
        "reopened_pose",
    ],
)
def test_wrong_reopen_or_close_identity_cannot_gain_saved_reopened_qualification(
    fault, monkeypatch
):
    from xpedition_cli import native_com_adapter as bridge
    from xpedition_cli import native_identity

    doc = Document()
    driver = driver_for(doc)
    plan = preview_for(driver)

    def reopen(app, path):
        app.ActiveDocument = app
        if fault == "wrong_board":
            app.FullName = str(path) + ".other"
        if fault == "stale_component_identity":
            app.rows[-1].UniqueId = app.rows[0].UniqueId
        if fault == "reopened_pose":
            app.rows[0].x += 1
        return None if fault == "missing_open_reply" else (app, [])

    if fault == "active_after_close":
        monkeypatch.setattr(doc, "Close", lambda saved: True)
    if fault == "wrong_pid":
        identities = iter((91, 92))
        monkeypatch.setattr(
            native_identity, "application_identity", lambda app, domain: {"pid": next(identities)}
        )
    monkeypatch.setattr(bridge, "_open_layout_document", reopen)
    report = execute_placement(request(), plan["state_digest"], driver)
    assert report["outcome"] == "persistence_incomplete"
    assert not report["persistence_verified"] and not report["verification"]["valid"]
    assert report["lifecycle"]["save_count"] == report["lifecycle"]["close_count"] == 1
    assert report["lifecycle"]["open_count"] == (0 if fault == "active_after_close" else 1)


def test_duplicate_uid_on_a_non_target_is_rejected_before_native_write():
    doc = Document()
    doc.rows[-1].UniqueId = doc.rows[0].UniqueId
    with pytest.raises(CLIError, match="identity is ambiguous"):
        driver_for(doc)
    assert not any(c.calls for c in doc.rows) and doc.saved == 0


def test_native_batch_never_opens_or_switches_an_unexpected_active_board(tmp_path, monkeypatch):
    from xpedition_cli import native_com_adapter as bridge
    from xpedition_cli import native_placement

    doc = Document()
    requested = tmp_path / "different.pcb"
    monkeypatch.setattr(bridge, "_layout_board_path", lambda params: requested)
    monkeypatch.setattr(bridge, "_application", lambda *args, **kwargs: doc)
    monkeypatch.setattr(
        bridge,
        "_open_layout_document",
        lambda *args: pytest.fail("preview must not open or switch a document"),
    )
    with pytest.raises(CLIError) as error:
        native_placement.run(
            {"project": str(requested), "request": request(), "apply": False}, None
        )
    assert error.value.details["write_attempted"] is False
    assert not any(c.calls for c in doc.rows) and doc.saved == 0
