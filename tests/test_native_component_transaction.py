"""Synthetic primitive boundaries only; no CAD, EXE, COM or database access."""

import copy
import json
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import native_component_transaction as T


class AdapterError(Exception):
    def __init__(self, code, message, details):
        self.code, self.message, self.details = code, message, details
        super().__init__(message)


REQUEST = {"layer": 1, "width": 0.254, "points": [[0, 0], [5, 0], [5, 3]], "unit": "mm"}


class BadIdentity:
    @property
    def UniqueId(self):
        raise RuntimeError("synthetic identity reply lost")


def case(*, start=True, put=None, end=True):
    events = []
    net = NS(UniqueId=17)
    trace = NS(UniqueId=123)

    def response(value):
        if isinstance(value, Exception):
            raise value
        return value

    class Doc:
        def TransactionStart(self, mode):
            events.append(("TransactionStart", mode))
            return response(start)

        def PutTrace(self, *args):
            events.append(("PutTrace", args))
            return response(trace if put is None else put)

        def TransactionEnd(self, keep):
            events.append(("TransactionEnd", keep))
            return response(end)

        def __getattr__(self, name):
            pytest.fail("Unexpected native operation: " + name)

    adapter = NS(
        UNIT_MM=4, AdapterError=AdapterError, _native_call=lambda name, function: function()
    )
    return Doc(), net, adapter, events


def invoke_failure(doc, net, adapter, request=None, result=None):
    with pytest.raises(AdapterError) as failure:
        T.put_trace_transaction(doc, net, request or copy.deepcopy(REQUEST), adapter, result=result)
    receipt = failure.value.details["native_result"]
    if result is not None:
        assert receipt is result
    return failure.value, receipt


def counts(result, name):
    counter = result["counters"][name]
    return counter["attempted"], counter["returned"]


def test_one_native_drc_transaction_has_exact_unit_order_and_known_identity():
    doc, net, adapter, events = case()
    receipt = {"caller_fact": "retained", "stages": {"caller_observation": "complete"}}
    result = T.put_trace_transaction(doc, net, REQUEST, adapter, result=receipt)
    assert result is receipt
    assert result["caller_fact"] == "retained"
    assert result["stages"]["caller_observation"] == "complete"
    assert [e[0] for e in events] == ["TransactionStart", "PutTrace", "TransactionEnd"]
    assert events[0][1] == 1 and events[2][1] is True
    assert events[1][1] == (
        1,
        net,
        0.254,
        3,
        ((0.0, 5.0, 5.0), (0.0, 0.0, 3.0), (0.0, 0.0, 0.0)),
        None,
        0,
        4,
    )
    assert result["net_uid"] == "17" and result["trace_uid"] == "123" and result["uid_known"]
    assert result["transaction_drc_acknowledged"] is True
    assert result["transaction_state_unknown"] is False
    assert result["transaction_state"] == "ended"
    assert all(counts(result, name) == (1, 1) for name in T._CALLS)


@pytest.mark.parametrize("reply", [False, None, 1, "true", RuntimeError("lost Start reply")])
def test_unacknowledged_start_never_puts_or_ends(reply):
    doc, net, adapter, events = case(start=reply)
    _, result = invoke_failure(doc, net, adapter)
    assert events == [("TransactionStart", 1)]
    assert counts(result, "TransactionStart") == (1, 0 if isinstance(reply, Exception) else 1)
    assert counts(result, "PutTrace") == counts(result, "TransactionEnd") == (0, 0)
    assert result["transaction_state_unknown"] and not result["uid_known"]


@pytest.mark.parametrize(
    "reply", [False, True, 0, "trace", RuntimeError("mutated then lost Put reply")]
)
def test_invalid_or_lost_put_reply_never_ends_or_persists(reply):
    doc, net, adapter, events = case(put=reply)
    _, result = invoke_failure(doc, net, adapter)
    assert [e[0] for e in events] == ["TransactionStart", "PutTrace"]
    assert counts(result, "PutTrace") == (1, 0 if isinstance(reply, Exception) else 1)
    assert counts(result, "TransactionEnd") == (0, 0)
    assert result["transaction_state_unknown"] and not result["uid_known"]


def test_missing_put_reply_is_distinct_from_false():
    doc, net, adapter, events = case()
    doc.PutTrace = lambda *args: None
    _, result = invoke_failure(doc, net, adapter)
    assert counts(result, "PutTrace") == (1, 1)
    assert result["native_calls_returned"][-1]["reply"] == {"type": "NoneType", "value": None}
    assert counts(result, "TransactionEnd") == (0, 0)
    assert events == [("TransactionStart", 1)]


@pytest.mark.parametrize(
    "trace", [BadIdentity(), NS(UniqueId=None), NS(UniqueId=False), NS(UniqueId=0)]
)
def test_trace_uid_failure_keeps_returned_put_fact_without_auto_end(trace):
    doc, net, adapter, _ = case(put=trace)
    _, result = invoke_failure(doc, net, adapter)
    assert result["native_trace_returned"] is True and not result["uid_known"]
    assert counts(result, "PutTrace") == (1, 1)
    assert counts(result, "Trace.UniqueId") == (1, 0 if isinstance(trace, BadIdentity) else 1)
    assert counts(result, "TransactionEnd") == (0, 0)
    assert result["transaction_state_unknown"]


@pytest.mark.parametrize("reply", [False, None, 1, "true", RuntimeError("lost End reply")])
def test_end_failure_is_never_retried_and_does_not_claim_rollback(reply):
    doc, net, adapter, events = case(end=reply)
    _, result = invoke_failure(doc, net, adapter)
    assert [e[0] for e in events] == ["TransactionStart", "PutTrace", "TransactionEnd"]
    assert counts(result, "TransactionEnd") == (1, 0 if isinstance(reply, Exception) else 1)
    assert result["uid_known"] is True and result["trace_uid"] == "123"
    assert result["transaction_state_unknown"] and not result["transaction_drc_acknowledged"]
    assert "rollback" not in result and "saved" not in result


@pytest.mark.parametrize("reply", [float("nan"), float("inf"), float("-inf")])
def test_invalid_native_numeric_reply_remains_json_serializable(reply):
    doc, net, adapter, _ = case(end=reply)
    _, result = invoke_failure(doc, net, adapter)
    json.dumps(result, allow_nan=False)
    assert result["native_calls_returned"][-1]["reply"] == {
        "type": "float",
        "nonfinite_value": str(reply),
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"layer": True},
        {"layer": 0},
        {"layer": 129},
        {"width": True},
        {"width": 0},
        {"width": -0.1},
        {"width": float("nan")},
        {"width": float("inf")},
        {"width": 10**1000},
        {"unit": "mil"},
        {"points": [[0, 0]]},
        {"points": [[0, 0], [0, 0]]},
        {"points": [[0, 0, 0], [1, 0]]},
        {"points": [[0, 0], [True, 1]]},
        {"points": [[0, 0], [float("inf"), 1]]},
        {"points": [[0, 0], ["1", 1]]},
        {"points": [[0, 0], [10**1000, 1]]},
        {"points": [[-1e308, 0], [1e308, 0]]},
        {"points": [[i, 0] for i in range(T.MAX_POINTS + 1)]},
    ],
)
def test_invalid_input_is_zero_native_dispatch(updates):
    doc, net, adapter, events = case()
    request = {**copy.deepcopy(REQUEST), **updates}
    error, result = invoke_failure(doc, net, adapter, request)
    assert error.code == "E_VALIDATION" and events == []
    assert not result["component_transaction_dispatched"]
    assert all(counts(result, name) == (0, 0) for name in T._CALLS)


@pytest.mark.parametrize(
    "net", [BadIdentity(), NS(UniqueId=None), NS(UniqueId=False), NS(UniqueId=0)]
)
def test_actual_net_uid_is_required_before_transaction(net):
    doc, _, adapter, events = case()
    _, result = invoke_failure(doc, net, adapter)
    assert events == [] and not result["component_transaction_dispatched"]
    assert counts(result, "TransactionStart") == (0, 0)


def test_unit_adapter_mismatch_cannot_change_native_unit():
    doc, net, adapter, events = case()
    adapter.UNIT_MM = 2
    _, result = invoke_failure(doc, net, adapter)
    assert events == [] and not result["component_transaction_dispatched"]


@pytest.mark.parametrize("end", [True, RuntimeError("lost End reply")])
def test_same_receipt_cannot_dispatch_again_after_success_or_failure(end):
    doc, net, adapter, events = case(end=end)
    receipt = {}
    if end is True:
        T.put_trace_transaction(doc, net, REQUEST, adapter, result=receipt)
    else:
        invoke_failure(doc, net, adapter, result=receipt)
    original, prior_events = copy.deepcopy(receipt), list(events)
    error, result = invoke_failure(doc, net, adapter, result=receipt)
    assert error.code == "E_CONFLICT" and result == original and events == prior_events


def test_supplied_dispatch_marker_refuses_before_any_native_observation():
    doc, net, adapter, events = case()
    original = {"component_transaction_dispatched": True, "uid_known": True, "trace_uid": "prior"}
    _, result = invoke_failure(doc, net, adapter, result=original)
    assert result is original and events == [] and result["trace_uid"] == "prior"


@pytest.mark.parametrize(
    "facts",
    [
        {"counters": {"PutTrace": {"attempted": 1, "returned": 0}}},
        {
            "native_calls_returned": [
                {"call": "TransactionEnd", "attempted": True, "returned": False}
            ]
        },
    ],
)
def test_prior_native_facts_also_refuse_even_without_dispatch_marker(facts):
    doc, net, adapter, events = case()
    before = copy.deepcopy(facts)
    error, result = invoke_failure(doc, net, adapter, result=facts)
    assert error.code == "E_CONFLICT" and result == before and events == []


@pytest.mark.parametrize(
    "facts",
    [
        {"counters": []},
        {"native_calls_returned": {}},
        {"stages": []},
        {"_untrusted": None},
    ],
)
def test_invalid_caller_receipt_is_returned_without_native_dispatch(facts):
    doc, net, adapter, events = case()
    error, result = invoke_failure(doc, net, adapter, result=facts)
    assert error.code == "E_VALIDATION" and result is facts and events == []


def test_native_error_code_and_existing_caller_facts_survive():
    native_error = AdapterError("E_TIMEOUT", "synthetic lost native reply", {})
    doc, net, adapter, _ = case(end=native_error)
    receipt = {"caller_fact": {"source": "frozen"}, "counters": {"Save": 0}}
    error, result = invoke_failure(doc, net, adapter, result=receipt)
    assert error.code == "E_TIMEOUT"
    assert result["caller_fact"] == {"source": "frozen"} and result["counters"]["Save"] == 0
    assert result["transaction_state_unknown"] and result["trace_uid"] == "123"
