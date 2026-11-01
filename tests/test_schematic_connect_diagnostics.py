"""151-shaped pure call fakes: no native/CAD/COM activation or old-state IO."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_native_owner as O
from xpedition_cli import native_com_adapter as A


class NativeFailure(Exception):
    def __init__(self):
        self.hresult = -2147352567
        super().__init__(
            self.hresult,
            "secret exception text",
            (
                0,
                "private/source/nonce",
                "secret argument and scope",
                None,
                13001,
                -2147467259,
            ),
            None,
        )


class Collection:
    def __init__(self, rows):
        self.rows = rows
        self.Count = len(rows)

    def Item(self, index):
        assert 1 <= index <= len(self.rows)
        return self.rows[index - 1]


class Pin:
    def __init__(self, number, x, y, fail=False):
        self.Number, self.x, self.y, self.fail = number, x, y, fail

    def GetLocation(self):
        if self.fail:
            raise NativeFailure()
        return NS(X=self.x, Y=self.y)


@pytest.fixture
def calls(monkeypatch):
    markers = []

    def invoke(name, function):
        markers.append(name)
        return function()

    monkeypatch.setattr(A, "_native_call", invoke)
    return markers


def _wire(block, first=None, second=None):
    return A._wire_between_pins(
        block,
        first or Pin("1", 300, 430),
        second or Pin("1", 600, 400),
        {"net": "RC_HIGH"},
        NS(constants=NS(VD_WIRE=7)),
    )


def test_declared_addnet_order_and_actual_call_markers_are_not_replaced_by_guessed_flags(calls):
    attempted = []
    first, second = Pin("1", 300, 430), Pin("1", 600, 400)

    class Block:
        def AddNet(self, x1, y1, x2, y2, pin1, pin2, bus_or_wire):
            attempted.append((x1, y1, x2, y2, pin1, pin2, bus_or_wire))
            return NS()

    net, _label = _wire(Block(), first, second)
    assert net is not None
    assert attempted == [
        (300, 430, 600, 430, first, None, 7),
        (600, 430, 600, 400, None, second, 7),
    ]
    assert calls == [
        "connect.VD_WIRE",
        "connect.first_pin.GetLocation",
        "connect.first_pin.X",
        "connect.first_pin.Y",
        "connect.second_pin.GetLocation",
        "connect.second_pin.X",
        "connect.second_pin.Y",
        "connect.AddNet.segment_0",
        "connect.AddNet.segment_1",
    ]


@pytest.mark.parametrize("failed_segment", [0, 1])
def test_segment_failure_retains_native_codes_exact_partial_counts_and_no_replay(
    calls, failed_segment
):
    attempted = []

    class Block:
        def AddNet(self, *args):
            index = len(attempted)
            attempted.append(args)
            if index == failed_segment:
                raise NativeFailure()
            return NS()

    with pytest.raises(A.AdapterError) as failure:
        _wire(Block())
    error = failure.value
    assert error.code == "E_SERVER"
    public = O._adapter_failure(error)["details"]
    assert public["action"] == "add_schematic_net"
    assert public["native"]["hresult"] == -2147352567
    assert public["native"]["scode_hex"] == "0x80004005"
    assert public["native"]["product_code"] == 13001
    assert public["native"]["source"] is None and public["native"]["source_redacted"] is True
    progress = public["schematic_connect_progress"]
    assert progress["segments_planned"] == 2
    assert progress["segments_attempted"] == failed_segment + 1
    assert progress["segments_returned"] == failed_segment
    assert progress["last_segment_index"] == failed_segment
    assert len(attempted) == failed_segment + 1
    assert public["native_call_unknown"] is True and public["automatic_replay"] is False
    assert "secret" not in json.dumps(public) and "private/source" not in json.dumps(public)


def test_pin_location_failure_stays_readback_unobserved_and_never_calls_addnet(calls):
    block = NS(AddNet=lambda *args: pytest.fail("no native write after failed GetLocation"))
    with pytest.raises(A.AdapterError) as failure:
        _wire(block, first=Pin("1", 300, 430, fail=True))
    public = O._adapter_failure(failure.value)["details"]
    assert public["action"] == "pin_location"
    progress = public["schematic_connect_progress"]
    assert progress["phase"] == "first_pin_location"
    assert progress["segments_planned"] is None
    assert progress["segments_attempted"] == progress["segments_returned"] == 0


def test_null_addnet_return_stops_the_remaining_segment_without_claiming_success(calls):
    attempted = []
    block = NS(AddNet=lambda *args: attempted.append(args))
    with pytest.raises(A.AdapterError) as failure:
        _wire(block)
    progress = O._adapter_failure(failure.value)["details"]["schematic_connect_progress"]
    assert len(attempted) == 1
    assert progress["segments_attempted"] == progress["segments_returned"] == 1
    assert progress["last_returned_nonnull"] is False


def test_label_failure_uses_declared_segment_string_long_long_shape(calls):
    segment = NS()
    attempted = []

    class Net:
        def GetSegments(self):
            return Collection([segment])

        def AddLabel(self, selected_segment, text, x, y):
            attempted.append((selected_segment, text, x, y))
            raise NativeFailure()

    with pytest.raises(A.AdapterError) as failure:
        A._label_schematic_net(Net(), "RC_HIGH", {"x": 450, "y": 490})
    assert attempted == [(segment, "RC_HIGH", 450, 490)]
    public = O._adapter_failure(failure.value)["details"]
    assert public["action"] == "label_schematic_net"
    assert public["schematic_connect_progress"] == {
        "phase": "add_label",
        "segment_observed": True,
        "label_attempted": True,
        "label_returned": False,
        "label_returned_nonnull": None,
    }


def test_duplicate_pin_identity_is_rejected_after_complete_formal_connection_census(calls):
    first, other = Pin("1", 0, 0), Pin("1", 1, 1)
    component = NS(GetConnections=lambda: Collection([NS(CompPin=first), NS(CompPin=other)]))
    with pytest.raises(A.AdapterError) as failure:
        A._designer_pin(component, "1")
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.details["matches"] == 2


def test_diagnostics_do_not_accept_arbitrary_action_native_text_or_progress_capabilities(calls):
    error = A.AdapterError(
        "E_SERVER",
        "secret message",
        {
            "action": "add_schematic_net",
            "native": {
                "hresult": True,
                "scode": None,
                "product_code": None,
                "source": "secret nonce and scope",
                "token": "secret",
            },
            "schematic_connect_progress": {
                "phase": "add_net",
                "segments_planned": 9000,
                "capability": "secret",
            },
        },
    )
    public = O._adapter_failure(error)["details"]
    assert public["native"]["hresult"] is None
    assert public["schematic_connect_progress_observed"] is False
    assert public["native_call_unknown"] is True
    assert "secret" not in json.dumps(public)
