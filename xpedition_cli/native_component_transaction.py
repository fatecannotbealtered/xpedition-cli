"""One-shot 151.0 trace insertion inside an explicit native DRC transaction.

This primitive has no eligibility, protection, persistence or rollback claim. Its
caller owns the isolated document and must independently observe the full result.
It never saves, closes, opens, selects objects or invokes a route engine.
"""

from __future__ import annotations

import math

UNIT_MM = 4
DRC_MODE = 1
MAX_POINTS = 10000
SOURCE = {
    "library": "MGCPCB 151.0",
    "TransactionStart": "DISPID117 METHOD flags0; (EPcbDRCMode=1) -> BOOL",
    "PutTrace": "DISPID65 METHOD flags0; "
    "layer,net,width,count,points,component,anchor,unit -> Trace",
    "TransactionEnd": "DISPID118 METHOD flags0; (bKeepChanges=True) -> BOOL",
    "boundary": "TransactionEnd applies the current DRC mode to modified data; "
    "False reports a violation and does not prove absence of residual changes.",
}
_STAGES = (
    "input",
    "net_identity",
    "transaction_start",
    "put_trace",
    "trace_identity",
    "transaction_end",
)
_CALLS = ("Net.UniqueId", "TransactionStart", "PutTrace", "Trace.UniqueId", "TransactionEnd")


def _number(value, label):
    if type(value) not in (int, float):
        raise ValueError(label + " must be a finite number")
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(label + " must be a finite number") from error
    if not math.isfinite(value):
        raise ValueError(label + " must be a finite number")
    return value


def _request(request):
    if not isinstance(request, dict):
        raise ValueError("Trace request must be an object")
    layer = request.get("layer")
    if type(layer) is not int or not 1 <= layer <= 128:
        raise ValueError("Trace layer must be an integer from 1 through 128")
    width = _number(request.get("width"), "Trace width")
    if width <= 0:
        raise ValueError("Trace width must be positive")
    if request.get("unit", "mm") != "mm":
        raise ValueError("Trace request coordinates and width must use mm")
    raw = request.get("points")
    if not isinstance(raw, (list, tuple)) or not 2 <= len(raw) <= MAX_POINTS:
        raise ValueError("Trace points require a bounded list of 2 through 10000 points")
    points = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Every trace point must have exactly two coordinates")
        points.append(tuple(_number(v, "Trace coordinate") for v in point))
    for first, second in zip(points, points[1:], strict=False):
        length = math.hypot(first[0] - second[0], first[1] - second[1])
        if not math.isfinite(length) or length == 0:
            raise ValueError("Every trace segment must have finite nonzero length")
    return layer, width, points


def _uid(value):
    if type(value) is int and value > 0:
        return str(value)
    if type(value) is str and value.strip() and value == value.strip() and value != "0":
        return value
    raise ValueError("Native UniqueId must be an actual nonzero identity")


def _reply(value):
    if type(value) is float and not math.isfinite(value):
        return {"type": "float", "nonfinite_value": str(value)}
    if value is None or type(value) in (bool, int, float, str):
        return {"type": type(value).__name__, "value": value}
    return {"type": type(value).__name__, "kind": "native_object"}


def _call(result, adapter, name, function, *, mutating=False):
    event = {"call": name, "attempted": True, "returned": False}
    result["native_calls_returned"].append(event)
    result["counters"][name]["attempted"] += 1
    if mutating:
        result["component_transaction_dispatched"] = True
        result["write_attempted"] = True
        result["transaction_state_unknown"] = True
    value = adapter._native_call("component_transaction." + name, function)
    result["counters"][name]["returned"] += 1
    event.update(returned=True, reply=_reply(value))
    return value


def put_trace_transaction(doc, net, request, adapter, *, result=None):
    """Return facts after one DRC transaction; raise with those same facts on failure.

    ``result`` may be the caller's mutable receipt. Each receipt is one-shot,
    including after reply loss; it cannot authorize retrying a native operation.
    Start/End need actual BOOL True. A returned Trace or End True is supplementary
    to the caller's material/protection checks and never establishes persistence.
    """
    if result is None:
        result = {}
    if not isinstance(result, dict):
        raise adapter.AdapterError(
            "E_VALIDATION", "Native result must be an object", {"native_result": {}}
        )
    for name, expected in (
        ("native_calls_returned", list),
        ("stages", dict),
        ("counters", dict),
        ("_untrusted", list),
    ):
        if name in result and not isinstance(result[name], expected):
            raise adapter.AdapterError(
                "E_VALIDATION",
                "Native result receipt containers are incomplete",
                {"native_result": result},
            )
    prior_calls = result.get("native_calls_returned", [])
    prior_counters = result.get("counters", {})
    prior_dispatch = any(
        isinstance(event, dict) and event.get("call") in _CALLS[1:] for event in prior_calls
    ) or any(
        isinstance(prior_counters.get(name), dict)
        and (prior_counters[name].get("attempted") or prior_counters[name].get("returned"))
        for name in ("TransactionStart", "PutTrace", "TransactionEnd")
    )
    if (
        result.get("component_transaction_entered")
        or result.get("component_transaction_dispatched")
        or prior_dispatch
    ):
        raise adapter.AdapterError(
            "E_CONFLICT",
            "This native transaction receipt cannot be replayed",
            {"native_result": result},
        )
    result["component_transaction_entered"] = True
    result["component_transaction_dispatched"] = False
    result.setdefault("native_calls_returned", [])
    result.setdefault("stages", {}).update(dict.fromkeys(_STAGES, "not_started"))
    result.setdefault("counters", {}).update(
        {name: {"attempted": 0, "returned": 0} for name in _CALLS}
    )
    result.update(
        uid_known=False,
        trace_uid=None,
        native_trace_returned=False,
        transaction_state="not_started",
        transaction_state_unknown=False,
        transaction_drc_acknowledged=False,
        transaction_source=dict(SOURCE),
    )
    result.setdefault("write_attempted", False)
    stage = "input"
    try:
        result["stages"][stage] = "started"
        layer, width, points = _request(request)
        if type(adapter.UNIT_MM) is not int or adapter.UNIT_MM != UNIT_MM:
            raise ValueError("Native adapter must explicitly use 151.0 millimetre unit 4")
        rows = (tuple(p[0] for p in points), tuple(p[1] for p in points), (0.0,) * len(points))
        result["transaction_request"] = {
            "layer": layer,
            "width": width,
            "points": [list(p) for p in points],
            "unit": "mm",
        }
        result["stages"][stage] = "complete"
        stage = "net_identity"
        result["stages"][stage] = "started"
        result["net_uid"] = _uid(_call(result, adapter, "Net.UniqueId", lambda: net.UniqueId))
        result["stages"][stage] = "complete"

        stage = "transaction_start"
        result["stages"][stage] = "started"
        started = _call(
            result,
            adapter,
            "TransactionStart",
            lambda: doc.TransactionStart(DRC_MODE),
            mutating=True,
        )
        if started is not True:
            raise ValueError(
                "TransactionStart did not return BOOL=True; PutTrace was not dispatched"
            )
        result.update(transaction_state="open", transaction_state_unknown=False)
        result["stages"][stage] = "acknowledged"

        stage = "put_trace"
        result["stages"][stage] = "started"
        trace = _call(
            result,
            adapter,
            "PutTrace",
            lambda: doc.PutTrace(layer, net, width, len(points), rows, None, 0, UNIT_MM),
            mutating=True,
        )
        if trace is None or type(trace) in (bool, int, float, str, bytes, list, tuple, dict):
            raise ValueError(
                "PutTrace did not return a native Trace object; TransactionEnd was not dispatched"
            )
        result["native_trace_returned"] = True
        result["stages"][stage] = "returned"
        stage = "trace_identity"
        result["stages"][stage] = "started"
        result["trace_uid"] = _uid(_call(result, adapter, "Trace.UniqueId", lambda: trace.UniqueId))
        result["uid_known"] = True
        result["stages"][stage] = "complete"

        stage = "transaction_end"
        result["stages"][stage] = "started"
        ended = _call(
            result, adapter, "TransactionEnd", lambda: doc.TransactionEnd(True), mutating=True
        )
        if ended is not True:
            raise ValueError(
                "TransactionEnd did not return BOOL=True; native outcome requires observation"
            )
        result.update(
            transaction_state="ended",
            transaction_state_unknown=False,
            transaction_drc_acknowledged=True,
        )
        result["stages"][stage] = "acknowledged"
        return result
    except Exception as error:
        result["stages"][stage] = "failed"
        if result["component_transaction_dispatched"]:
            result["transaction_state_unknown"] = True
        code = getattr(error, "code", None) or (
            "E_VERIFY_INCOMPLETE"
            if result["component_transaction_dispatched"]
            else "E_VALIDATION"
            if isinstance(error, (ValueError, TypeError, KeyError))
            else "E_NATIVE_FAILURE"
        )
        result["transaction_failure"] = {
            "stage": stage,
            "code": code,
            "cause": type(error).__name__,
            "message": str(error),
        }
        untrusted = result.setdefault("_untrusted", [])
        for name in ("native_calls_returned", "transaction_failure"):
            if name not in untrusted:
                untrusted.append(name)
        raise adapter.AdapterError(
            code,
            "Native trace transaction incomplete; retain facts and do not replay",
            {
                "native_result": result,
                "write_attempted": result["component_transaction_dispatched"],
            },
        ) from error
