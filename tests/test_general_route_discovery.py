"""Existing public inspect discovery invokes only the attached observation chain."""

import copy
import sys
from types import SimpleNamespace as NS

import pytest
from test_board_general import LateBound, config, general_case
from test_driver_v2 import _scope
from test_routepass_live_inspect import native_case

from xpedition_cli import board_general as G
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime


def test_public_general_discovery_is_available_before_any_endpoint_plan(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic")
    calls = []

    class Backend:
        read_timeout_seconds = 600

        def invoke(self, method, params, **kwargs):
            calls.append((method, params))
            return {
                "pcb": str(board),
                "observation": {
                    "native_general_state": {
                        "engineering_write_dispatched": False,
                        "selection_write_dispatched": False,
                        "persistence": "unknown",
                        "fromto_inventory": [],
                        "configuration": config(),
                    }
                },
            }

    monkeypatch.setattr(pcb, "reader", lambda _: Backend())
    with runtime(Adapter(Backend()), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "pcb",
                "inspect",
                "--project",
                str(board),
                "--live-geometry",
                "--pid",
                "42",
                "--nets",
                "GND",
                "--layers",
                "1",
                "--native-general-state",
            ]
        )
    assert result.envelope["ok"], result.envelope
    assert calls == [
        (
            "board_info",
            {
                "project": str(board),
                "start": False,
                "geometry": True,
                "pid": 42,
                "routepass_request": {"net": "GND", "layer": 1},
                "native_general_state": True,
            },
        )
    ]


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--live-geometry", "--pid", "42"],
        ["--live-geometry", "--pid", "42", "--nets", "A_IN", "--layers", "1"],
        ["--live-geometry", "--pid", "42", "--nets", "GND", "--layers", "1,2"],
    ],
)
def test_invalid_public_general_discovery_stops_before_adapter(tmp_path, monkeypatch, extra):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic")

    class Backend:
        def invoke(self, *_args, **_kwargs):
            pytest.fail("invalid read must not invoke adapter")

    with runtime(Adapter(Backend()), state_dir=tmp_path / "state") as cli:
        result = cli.run(
            ["pcb", "inspect", "--project", str(board), "--native-general-state", *extra]
        )
    assert result.envelope["error"]["code"] == "E_VALIDATION", result.envelope


@pytest.mark.parametrize(
    "fault", [None, "configuration_drift", "fromto_drift", "getter", "configuration_getter"]
)
def test_actual_live_adapter_chain_brackets_general_discovery_without_selection_or_write(
    tmp_path, monkeypatch, fault
):
    board, state, request, events = native_case(tmp_path, monkeypatch)
    plan, _ = general_case()
    state.clear()
    state.update(copy.deepcopy(plan["before"]))
    request.clear()
    request.update(G.material_request(plan["request"]))
    reads = []

    def configuration(*_args):
        reads.append("configuration")
        if fault == "configuration_getter":
            error = ValueError("exact located supported configuration failure")
            error.configuration_observation = {
                "supported_fields_complete": False,
                "failed_field": {"field": "push_and_shove.3", "dispid": 4, "arguments": [3]},
                "completed_fields": ["layer_count"],
            }
            raise error
        result = config()
        if fault == "configuration_drift" and reads.count("configuration") > 1:
            result["route_max_vias"] += 1
        return result

    def fromtos(*_args, **_kwargs):
        reads.append("fromtos")
        if fault == "getter":
            error = ValueError("actual False endpoint getter unavailable")
            error.fromto_observation = {"complete": False, "failed_observation_index": 0}
            raise error
        rows = copy.deepcopy(plan["fromto_inventory"])
        if fault == "fromto_drift" and reads.count("fromtos") > 1:
            rows[0]["from_layer"] = 2
        return [(row, object(), i) for i, row in enumerate(rows)]

    monkeypatch.setattr(G, "observe_configuration", configuration)
    monkeypatch.setattr(G, "observe_fromtos", fromtos)
    monkeypatch.setattr(
        G,
        "observe_selection",
        lambda *_args: {
            "complete": True,
            **dict.fromkeys(("nets", "components", "pins", "traces", "vias", "fromtos"), []),
        },
    )
    monkeypatch.setattr(G, "execute", lambda *_args: pytest.fail("discovery cannot execute"))
    params = {
        "project": str(board),
        "pid": 42,
        "routepass_request": request,
        "native_general_state": True,
    }
    if fault is not None:
        with pytest.raises(A.AdapterError) as raised:
            A._board_observation(params, object())
        observed = raised.value.details["native_general_state"]
        if fault == "configuration_getter":
            assert observed["state"] is None
            assert observed["configuration"]["failed_field"]["field"] == "push_and_shove.3"
            assert "complete_material_read" not in events
        else:
            assert observed["state"] == state and observed["configuration"] == config()
        assert observed["engineering_write_dispatched"] is False
        if fault == "getter":
            assert observed["fromto_read_error"]["failed_observation_index"] == 0
        return
    result = A._board_observation(params, object())
    observed = result["observation"]["native_general_state"]
    assert observed["configuration"] == observed["configuration_after"]
    assert observed["fromto_inventory"] == observed["fromto_inventory_after"]
    assert observed["components"]["valid"] and len(observed["components"]["components"]) == 3
    assert observed["selection"] == observed["selection_after"]
    assert "after_complete_material" in observed["selection_consistency"]
    assert "no_material_time_window_or_atomic_freeze" in observed["selection_consistency"]
    assert observed["state"] == state
    assert observed["selection_write_dispatched"] is False
    assert observed["engineering_write_dispatched"] is False
    assert events == ["capture", "complete_material_read", "capture"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "foreign_selected",
        "foreign_component",
        "collection",
        "count",
        "second_count",
        "item",
        "uid",
        "selected",
        "name",
        "fromto_selection",
        "drift",
    ],
)
def test_real_selected_collection_chain_preserves_failures_and_reports_foreign_selection(
    tmp_path, monkeypatch, fault
):
    board, state, request, events = native_case(tmp_path, monkeypatch)
    plan, _ = general_case()
    state.clear()
    state.update(copy.deepcopy(plan["before"]))
    request.clear()
    request.update(G.material_request(plan["request"]))
    actual_doc = A._application(object(), attach_only=True, quiet=False).ActiveDocument
    selection_reads = []
    calls = []
    monkeypatch.setitem(sys.modules, "pythoncom", NS(DISPATCH_PROPERTYGET=2))

    class SelectedObject:
        @property
        def UniqueId(self):
            if fault == "uid":
                raise RuntimeError("native UniqueId failed")
            return "foreign-net" if fault == "foreign_selected" else "n1"

        @property
        def Selected(self):
            if fault == "selected":
                raise RuntimeError("native Selected failed")
            return not (fault == "drift" and len(selection_reads) == 2)

        @property
        def Name(self):
            if fault == "name":
                raise RuntimeError("native Name failed")
            return "B_OUT" if fault == "foreign_selected" else "GND"

    class Collection:
        def __init__(self, rows, *, name):
            self.rows, self.name = rows, name

        @property
        def Count(self):
            if self.name == "nets" and (
                fault == "count" or (fault == "second_count" and len(selection_reads) == 2)
            ):
                raise RuntimeError("native Count failed")
            return len(self.rows)

        def Item(self, index):
            if fault == "item" and self.name == "nets":
                raise RuntimeError("native Item failed")
            return self.rows[index - 1]

    names = {23: "nets", 22: "components", 26: "pins", 27: "traces", 28: "vias"}

    def native_get(dispid, args):
        calls.append((dispid, args))
        name = names[dispid]
        if dispid == 23:
            selection_reads.append(1)
            if fault == "collection":
                raise RuntimeError("native Nets PROPERTYGET failed")
        return Collection(
            [SelectedObject()]
            if dispid == 23 or (fault == "foreign_component" and dispid == 22)
            else [],
            name=name,
        )

    actual_doc._oleobj_ = LateBound(native_get)
    monkeypatch.setattr(G, "observe_configuration", lambda *_args: config())

    def fromtos(*_args, **kwargs):
        if kwargs.get("selected"):
            if fault == "fromto_selection":
                error = RuntimeError("selected FromTo endpoint getter failed")
                error.fromto_observation = {
                    "complete": False,
                    "failed_observation_index": 0,
                    "collection_complete": False,
                }
                raise error
            return []
        return [(copy.deepcopy(plan["selected_fromto"]), object(), 0)]

    monkeypatch.setattr(G, "observe_fromtos", fromtos)
    monkeypatch.setattr(G, "execute", lambda *_args: pytest.fail("read cannot execute"))
    params = {
        "project": str(board),
        "pid": 42,
        "routepass_request": request,
        "native_general_state": True,
    }
    if fault not in (None, "foreign_selected", "foreign_component"):
        with pytest.raises(A.AdapterError) as raised:
            A._board_observation(params, object())
        observed = raised.value.details["native_general_state"]
        assert observed["state"] == state
        assert observed["engineering_write_dispatched"] is False
        assert observed["selection_write_dispatched"] is False
        if fault == "drift":
            assert raised.value.code == "E_CONFLICT"
            assert observed["selection"]["nets"][0]["selected"] is True
            assert observed["selection_after"]["nets"][0]["selected"] is False
            assert observed["selection_consistency"] is None
        else:
            error = observed["selection_read_error"]
            assert error["sample"] == (
                "selection_after" if fault == "second_count" else "selection"
            )
            failure = error["observation"]
            assert failure["complete"] is False
            field = failure["failed_field"]
            assert field["collection"] == ("fromtos" if fault == "fromto_selection" else "nets")
            assert (
                field["field"]
                == {
                    "collection": "PROPERTYGET",
                    "count": "Count",
                    "second_count": "Count",
                    "item": "Item",
                    "uid": "UniqueId",
                    "selected": "Selected",
                    "name": "Name",
                    "fromto_selection": "selected_inventory",
                }[fault]
            )
            if fault in ("selected", "name"):
                assert failure["observed"]["nets"][0]["uid"] == "n1"
            if fault == "second_count":
                assert observed["selection"]["complete"] is True
            if fault == "fromto_selection":
                assert error["fromto_read_error"]["failed_observation_index"] == 0
                assert failure["completed_collections"] == [
                    "nets",
                    "components",
                    "pins",
                    "traces",
                    "vias",
                ]
            assert observed["selection_after"] is None
        assert events == ["capture", "complete_material_read"]
        return
    result = A._board_observation(params, object())
    observed = result["observation"]["native_general_state"]
    assert observed["selection"] == observed["selection_after"]
    assert observed["selection"]["complete"] is True
    if fault == "foreign_selected":
        assert observed["selection"]["nets"] == [
            {"uid": "foreign-net", "selected": True, "name": "B_OUT"}
        ]
    if fault == "foreign_component":
        assert observed["selection"]["components"] == [{"uid": "n1", "selected": True}]
    assert (
        calls
        == [
            (23, (1, True, "*")),
            (22, (1, -1, 4, "*")),
            (26, (1,)),
            (27, (1, 0)),
            (28, (1,)),
        ]
        * 2
    )
    assert events == ["capture", "complete_material_read", "capture"]


@pytest.mark.parametrize("apply", [True, 1, "true"])
def test_direct_general_apply_is_refused_before_attachment_or_material_observation(
    monkeypatch, apply
):
    monkeypatch.setattr(A, "_application", lambda *_a, **_k: pytest.fail("cannot attach"))
    monkeypatch.setattr(G, "execute", lambda *_a, **_k: pytest.fail("cannot execute"))
    with pytest.raises(A.AdapterError) as raised:
        A._route_board({"mode": "bounded-general", "apply": apply}, object())
    assert raised.value.code == "E_UNSUPPORTED"
    assert "vCommand" in raised.value.message
    assert raised.value.details == {
        "write_attempted": False,
        "native_dispatch_supported": False,
        "engineering_write_dispatched": False,
        "selection_write_dispatched": False,
        "save_close_open_dispatched": False,
    }


def test_general_adapter_preview_marks_selector_limit_without_changing_nested_binding(
    monkeypatch,
):
    nested = {"fingerprint": "a" * 64, "can_apply": True}
    result = {"general_route": nested, "state_digest": nested["fingerprint"]}
    monkeypatch.setattr(G, "execute", lambda *_a: result)
    observed = A._route_board({"mode": "bounded-general", "apply": False}, object())
    assert observed["general_route"] is nested
    assert nested == {"fingerprint": "a" * 64, "can_apply": True}
    assert observed["native_dispatch_supported"] is False
    assert "numeric35367" in observed["native_dispatch_limitation"]
