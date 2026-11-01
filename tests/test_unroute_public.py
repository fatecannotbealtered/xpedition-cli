"""Public unroute recovery semantics; native facts here are synthetic fixtures."""

import copy
import hashlib
import json

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def fixture_state():
    traces = [
        {"kind": "trace", "net": net, "layer": 1, "width": 0.254, "path": [[0, y, 0], [5, y, 0]]}
        for net, y in [("A", 0), ("B", 3)]
    ]
    return {
        "geometry": {
            "components": [
                {
                    "refdes": ref,
                    "uid": uid,
                    "pins": [
                        {"number": "1", "net": "A", "x": x, "y": 0},
                        {"number": "2", "net": "B", "x": x, "y": 3},
                    ],
                    "protected_geometry": {"copper": "fixture", "holes": [1.0]},
                }
                for ref, uid, x in [("J1", "67", 0), ("R1", "62", 5)]
            ],
            "stackup": [
                {"layer": 1, "kind": "conductor", "usage": "signal"},
                {"layer": 2, "kind": "conductor", "usage": "signal"},
            ],
            "model": {
                "layers": 2,
                "outline": [[-2, -2], [10, 10]],
                "holes": [[0, 0, 1]],
                "traces": traces,
                "vias": [],
                "via_holes": [],
                "planes": [],
                "unrouted": [],
                "connectivity": {
                    "complete": True,
                    "nets": [
                        {"net": n, "uid": str(i), "routed": True, "opens": 0}
                        for i, n in [(11, "A"), (12, "B")]
                    ],
                },
            },
        },
        "routing_objects": [
            {"kind": "trace", "uid": str(i), "net": t["net"], "geometry": copy.deepcopy(t)}
            for i, t in zip([91, 92], traces, strict=True)
        ],
    }


def remove(state, nets):
    state["routing_objects"] = [r for r in state["routing_objects"] if r["net"] not in nets]
    model = state["geometry"]["model"]
    model["traces"] = [r for r in model["traces"] if r["net"] not in nets]
    for row in model["connectivity"]["nets"]:
        if row["net"] in nets:
            row.update(opens=1, routed=False)
    model["unrouted"] = [copy.deepcopy(r) for r in model["connectivity"]["nets"] if r["opens"]]


def bound(state, nets):
    selected = [copy.deepcopy(r) for r in state["routing_objects"] if r["net"] in nets]
    return {
        "before": copy.deepcopy(state),
        "selected_objects": selected,
        "nets": nets,
        "expected_connectivity": [
            {"net": n, "uid": str(11 if n == "A" else 12), "opens": 1, "routed": False}
            for n in nets
        ],
        "fingerprint": digest({"state": state, "nets": nets}),
    }


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "zero",
        "partial",
        "save",
        "reopen",
        "observer",
        "lost_reply",
        "drift_uid",
        "drift_file",
        "protected_hole",
        "wrong_opens",
        "receipt_store",
        "partial_observer",
        "save_observer",
    ],
)
def test_public_unroute_receipt_and_new_process_query(tmp_path, monkeypatch, fault):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "board.pcb"
    board.write_bytes(b"synthetic-board")
    state = fixture_state()
    if fault == "zero":
        remove(state, {"A"})
    partial = fault in {"partial", "partial_observer"}
    save_failure = fault in {"save", "save_observer"}
    selected = ["A", "B"] if partial else ["A"]
    calls = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            if method == "identity":
                if calls and fault in {"observer", "partial_observer", "save_observer"}:
                    raise CLIError("E_TIMEOUT", "injected post-handler observation failure")
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                return copy.deepcopy(state)
            assert method == "unroute_nets"
            plan = bound(state, selected)
            preview = {
                "pcb": str(board),
                "nets": selected,
                "to_delete": {"traces": len(plan["selected_objects"]), "vias": 0},
                "targets": [{"target": n, "traces": 1, "vias": 0} for n in selected],
                "unroute": plan,
            }
            if not params.get("apply"):
                return preview
            calls.append("apply")
            if "expected" in params:
                assert params["expected"] == plan["fingerprint"]
            if fault != "zero":
                remove(state, {"A"})
            if fault == "protected_hole":
                state["geometry"]["model"]["holes"][0][2] = 1.2
            if fault == "wrong_opens":
                state["geometry"]["model"]["connectivity"]["nets"][0]["opens"] = 0
            no_change = fault == "zero"
            result = {
                **preview,
                "unroute": plan,
                "applied": not no_change,
                "no_change": no_change,
                "saved": not save_failure and fault != "zero",
                "change_status": "not_applied"
                if no_change
                else "partial"
                if partial
                else "applied",
                "persistence_state": "unchanged"
                if no_change
                else "unknown"
                if save_failure or fault == "reopen"
                else "saved",
                "persistence_verified": not save_failure and fault not in {"zero", "reopen"},
                "state_after": copy.deepcopy(state),
                "geometry_after": copy.deepcopy(state["geometry"]),
                "deleted": 0 if no_change else 1,
                "removed_objects": [] if no_change else [plan["selected_objects"][0]],
            }
            if fault == "lost_reply":
                raise CLIError("E_TIMEOUT", "synthetic native reply lost; no returned witness")
            if partial or save_failure or fault == "reopen":
                raise CLIError(
                    "E_PARTIAL" if partial else "E_NATIVE_FAILURE",
                    "injected boundary",
                    {"native_result": result},
                )
            return result

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "routing",
        "unroute",
        "--project",
        str(board),
        "--nets",
        ",".join(selected),
        "--dangerous",
    ]
    state_dir = tmp_path / "state"
    with runtime(Adapter(backend), state_dir=state_dir) as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        op = plan.envelope["data"]["preview"]["operation_id"]
        token = plan.envelope["data"]["confirm_token"]
        if fault == "receipt_store":
            from xpedition_cli._vendor.tool_driver_sdk.store import StateStore, StoreError

            finish = StateStore.finish
            failed = []

            def fail_receipt(store, operation, status, receipt=None, extra=None):
                if receipt is not None and not failed:
                    failed.append(True)
                    raise StoreError("synthetic final receipt write failure")
                return finish(store, operation, status, receipt, extra)

            monkeypatch.setattr(StateStore, "finish", fail_receipt)
        if fault == "drift_uid":
            state["routing_objects"][0]["uid"] = "replacement"
        if fault == "drift_file":
            board.write_bytes(b"changed-since-preview")
        result = cli.run([*args, "--confirm", token])
        envelope = result.envelope
        if fault == "receipt_store":
            assert not envelope["ok"]
            assert envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
            duplicate = cli.run([*args, "--confirm", token])
            assert not duplicate.envelope["ok"]
            assert calls == ["apply"]
            with runtime(Adapter(backend), state_dir=state_dir) as observer:
                queried = observer.run(["operation", "show", "--id", op])
                stored = queried.envelope["data"]["operation_status"]
                assert stored["status"] == "unknown" and stored["receipt"] is None
            assert state["geometry"]["model"]["connectivity"]["nets"][0]["opens"] == 1
            return
        if fault in {"drift_uid", "drift_file"}:
            assert not envelope["ok"]
            assert envelope["error"]["code"] == "E_CONFLICT"
            assert envelope["error"]["details"]["dispatch_state"] == "not_started"
            assert not calls
            return
        receipt = (
            envelope["data"]["receipt"]
            if envelope["ok"]
            else envelope["error"]["details"]["receipt"]
        )
        outcome = receipt["outcome"]
        if fault in {None, "zero"}:
            assert envelope["ok"], envelope
            assert outcome["verification"]["status"] == "passed"
            assert outcome["persistence"] == ("unchanged" if fault == "zero" else "saved")
            assert outcome["change"] == ("not_applied" if fault == "zero" else "applied")
            assert receipt["extensions"]["file_readback"]["engineering_complete"] is False
        else:
            assert not envelope["ok"]
            assert outcome["verification"]["status"] in {"incomplete", "failed"}
            if save_failure or fault in {"reopen", "lost_reply"}:
                assert outcome["persistence"] == "unknown"
            else:
                assert outcome["persistence"] == "saved"
            if partial:
                assert outcome["change"] == "partial"
                if fault == "partial":
                    assert receipt["extensions"]["persistence_independently_reopened"] is True
        duplicate = cli.run([*args, "--confirm", token])
        assert not duplicate.envelope["ok"]
        assert calls == ["apply"]
    with runtime(Adapter(backend), state_dir=state_dir) as cli:
        queried = cli.run(["operation", "show", "--id", op])
        assert queried.envelope["ok"]
        stored = queried.envelope["data"]["operation_status"]
        assert stored["receipt"]["outcome"] == outcome
