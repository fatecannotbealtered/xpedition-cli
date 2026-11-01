"""Public SDK copper receipts; all native geometry and failures are synthetic."""

import copy
import json
import os
import subprocess
import sys

import pytest
from test_driver_v2 import _scope, _worker_fixture
from test_unroute_public import fixture_state

from xpedition_cli import board_copper as C
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


def copper_state():
    state = fixture_state()
    state.pop("routing_objects")
    state["geometry"]["model"]["outline"] = [
        {
            "kind": "outline",
            "path": [[-2, -2, 0], [10, -2, 0], [10, 10, 0], [-2, 10, 0], [-2, -2, 0]],
        }
    ]
    state["plane_state"] = {
        "shapes": [],
        "assignments": [],
        "generated": [],
        "classes": [{"name": "Default", "parent": None, "fixture": True}],
    }
    return state


def rectangle(x1=-1, y1=-1, x2=9, y2=9):
    return {"path": [[x1, y1, 0], [x2, y1, 0], [x2, y2, 0], [x1, y2, 0], [x1, y1, 0]]}


def generated_state(before, *, draft_only=False, partial=False):
    state = copy.deepcopy(before)
    target = {"net": "A", "net_uid": "11", "layer": 2}
    state["plane_state"]["shapes"] = [
        {**target, "uid": "301", "route_obstructed": False, "geometry": rectangle()}
    ]
    state["plane_state"]["assignments"] = [
        {
            **target,
            "plane_class": "Default",
            "state": 2 if draft_only else 1,
            "route_border_as_shape": False,
        }
    ]
    if not draft_only:
        shapes = [rectangle(-1, -1, 4, 9), rectangle(6, -1, 9, 9)] if partial else [rectangle()]
        generated = []
        for shape in shapes:
            path = shape["path"]
            bounds = [
                min(p[0] for p in path),
                min(p[1] for p in path),
                max(p[0] for p in path),
                max(p[1] for p in path),
            ]
            circles = [
                [x, y, radius]
                for x, y, radius in [(0, 3, 0.5), (5, 3, 0.5), (0, 0, 0.5)]
                if bounds[0] < x - radius < x + radius < bounds[2]
                and bounds[1] < y - radius < y + radius < bounds[3]
            ]
            # Complete contour and cutout evidence, not only a count/bounding box.
            actual = {**shape, "circular_cutouts": circles}
            generated.append(
                {
                    **target,
                    "geometry": actual,
                    "bounds_mm": bounds,
                    "filled": True,
                    "hatch_pattern": 0,
                    "point_witnesses": [
                        {
                            "kind": "pin",
                            "refdes": ref,
                            "pin": "2",
                            "net": "B",
                            "xy_mm": [x, 3],
                            "outer_contains": bounds[0] <= x <= bounds[2],
                            "cutout_contains": [cx == x and cy == 3 for cx, cy, _ in circles],
                            "copper_at_point": False,
                        }
                        for ref, x in [("J1", 0), ("R1", 5)]
                    ],
                    "identity_basis": "native net UID/layer and complete geometry content",
                }
            )
        state["plane_state"]["generated"] = generated
        state["geometry"]["model"]["planes"] = [
            {"kind": "plane", "net": "A", "layer": 2, **row["geometry"]} for row in generated
        ]
    return state


def query_operation(operation):
    command = [
        sys.executable,
        "-m",
        "xpedition_cli",
        "operation",
        "show",
        "--id",
        operation,
        "--compact",
    ]
    process = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=30, env=os.environ.copy()
    )
    assert process.returncode == 0, process.stderr
    envelope = json.loads(process.stdout)
    assert envelope["ok"], envelope
    return {"command": command, "returncode": process.returncode, "envelope": envelope}


def public_case(tmp_path, monkeypatch, fault=None):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic-copper-board")
    state = copper_state()
    events, reads, dispatches = [], [], []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, options, **kwargs):
            nonlocal state
            if method == "identity":
                if dispatches and fault in {
                    "observer_saved",
                    "observer_partial",
                    "observer_save_failed",
                    "lost_reply_observer",
                }:
                    raise CLIError(
                        "E_TIMEOUT", "injected subsequent independent observation failure"
                    )
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                assert options.get("plane_objects") is True
                assert options.get("strict") is True
                reads.append(copy.deepcopy(options))
                return copy.deepcopy(state)
            assert method == "plane_pour", method
            if options.get("replace") or options.get("remove"):
                raise CLIError(
                    "E_UNSUPPORTED",
                    "Synthetic existing copper subset refuses replacement/removal",
                    {"write_attempted": False},
                )
            try:
                plan = C.bind(
                    copy.deepcopy(state), options["net"], options["layer"], options["margin"], A
                )
            except A.AdapterError as error:
                raise CLIError(
                    error.code, str(error), {**(error.details or {}), "write_attempted": False}
                ) from error
            stages = dict.fromkeys(
                (
                    "create",
                    "observe_created",
                    "generate",
                    "observe_generated",
                    "protection",
                    "save",
                    "close",
                    "reopen",
                ),
                "not_started",
            )
            result = {
                "pcb": str(board),
                "net": "A",
                "layer": 2,
                "copper": plan,
                "applied": False,
                "change_status": "not_applied",
                "saved": False,
                "persistence_state": "unchanged",
                "persistence_verified": False,
                "created_shape_uid": None,
                "stages": stages,
            }
            if not options.get("apply"):
                return result
            assert options["expected"] == plan["fingerprint"]
            dispatches.append("plane_pour")
            events.append("create")
            stages.update(create="returned", observe_created="complete", generate="started")
            draft_only = fault in {"generation_failed", "observer_partial"}
            partial = fault == "generation_partial"
            state = generated_state(state, draft_only=draft_only, partial=partial)
            events.append("generate")
            stages.update(
                generate="failed" if draft_only else "returned",
                observe_generated="complete",
                protection="failed" if draft_only or partial else "passed",
            )
            result.update(
                applied=True,
                change_status="partial" if draft_only or partial else "applied",
                persistence_state="unknown",
                created_shape_uid="301",
                state_after=copy.deepcopy(state),
                geometry_after=copy.deepcopy(state["geometry"]),
            )
            result["protection"] = C.compare(plan, state, "301")
            if draft_only or partial:
                result["post_write_error"] = {
                    "stage": "generate",
                    "code": "E_PARTIAL",
                    "cause": "SyntheticGenerationFailure",
                }
                raise CLIError(
                    "E_PARTIAL",
                    "Injected draft-only or multiple generated regions",
                    {"native_result": result},
                )
            events.append("save")
            stages["save"] = "started"
            if fault in {"save_failed", "observer_save_failed"}:
                result["post_write_error"] = {
                    "stage": "save",
                    "code": "E_NATIVE_FAILURE",
                    "cause": "SyntheticSaveFailure",
                }
                raise CLIError(
                    "E_NATIVE_FAILURE", "Injected save failure", {"native_result": result}
                )
            events.extend(["close", "reopen"])
            stages.update(save="acknowledged", close="confirmed", reopen="started")
            result["saved"] = True
            if fault == "reopen_failed":
                result["post_write_error"] = {
                    "stage": "reopen",
                    "code": "E_NATIVE_FAILURE",
                    "cause": "SyntheticReopenFailure",
                }
                raise CLIError(
                    "E_NATIVE_FAILURE", "Injected reopen failure", {"native_result": result}
                )
            stages["reopen"] = "verified"
            result.update(persistence_state="saved", persistence_verified=True)
            if fault in {"lost_reply", "lost_reply_observer"}:
                raise CLIError(
                    "E_TIMEOUT",
                    "Injected transport lost the native reply; no save witness returned",
                )
            return result

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "copper",
        "pour",
        "--project",
        str(board),
        "--net",
        "A",
        "--layer",
        "2",
        "--margin",
        "1",
    ]
    return backend, args, events, reads, dispatches, lambda: state, board


def test_public_copper_preview_warns_before_drawing_without_changing_labels(tmp_path, monkeypatch):
    backend, args, events, _, dispatches, get_state, _ = public_case(tmp_path, monkeypatch)
    labels = [
        {"kind": "refdes", "refdes": "R1", "side": "top", "extents": [3, 3, 4, 4]},
        {"kind": "refdes", "refdes": "J1", "side": "top", "extents": [12, 12, 13, 13]},
    ]
    get_state()["geometry"]["model"]["texts"] = copy.deepcopy(labels)
    with runtime(Adapter(backend)) as cli:
        result = cli.run([*args, "--dry-run"]).envelope
    assert result["ok"], result
    preview = result["data"]["preview"]["extensions"]["legacy_preview"]
    planned = [
        r
        for r in preview["layout_reminders"]
        if r["kind"] == "projected_text_planned_plane_overlap"
    ]
    assert [(r["refdes"], r["plane_layer"]) for r in planned] == [("R1", 2)]
    assert get_state()["geometry"]["model"]["texts"] == labels
    assert not events and not dispatches


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "generation_failed",
        "generation_partial",
        "save_failed",
        "reopen_failed",
        "observer_saved",
        "observer_partial",
        "observer_save_failed",
        "lost_reply",
        "lost_reply_observer",
        "receipt_store",
    ],
)
def test_public_copper_receipt_and_new_process_query(tmp_path, monkeypatch, fault):
    backend, args, events, reads, dispatches, current_state, _ = public_case(
        tmp_path, monkeypatch, fault
    )
    with runtime(Adapter(backend)) as cli:
        plan = cli.run([*args, "--dry-run"]).envelope
        assert plan["ok"], plan
        assert not events and not dispatches
        preview = plan["data"]["preview"]
        copper = preview["extensions"]["legacy_preview"]["copper"]
        assert copper["target"] == {
            "net": "A",
            "net_uid": "11",
            "layer": 2,
            "layer_usage": "signal",
        }
        assert copper["rectangle"] == [-1, -1, 9, 9]
        assert not copper["before"]["plane_state"]["generated"]
        token, operation = plan["data"]["confirm_token"], preview["operation_id"]
        if fault == "receipt_store":
            from xpedition_cli._vendor.tool_driver_sdk.store import StateStore, StoreError

            finish, failures = StateStore.finish, []

            def fail_first_receipt(store, op, status, receipt=None, extra=None):
                if receipt is not None and not failures:
                    failures.append(True)
                    raise StoreError("Injected copper final receipt write failure")
                return finish(store, op, status, receipt, extra)

            monkeypatch.setattr(StateStore, "finish", fail_first_receipt)
        confirmed = cli.run([*args, "--confirm", token]).envelope
        assert dispatches == ["plane_pour"]
        after_dispatch_events = list(events)
        repeat = cli.run([*args, "--confirm", token]).envelope
        assert not repeat["ok"]
        assert events == after_dispatch_events and dispatches == ["plane_pour"]
    query = query_operation(operation)
    stored = query["envelope"]["data"]["operation_status"]
    assert events == after_dispatch_events and dispatches == ["plane_pour"]
    assert reads, "The public driver must bind strict plane state before writing"
    if fault == "receipt_store":
        assert confirmed["error"]["code"] == "E_OUTCOME_UNKNOWN"
        assert stored["status"] == "unknown" and stored["receipt"] is None
        assert current_state()["plane_state"]["generated"]
    else:
        receipt = (
            confirmed["data"]["receipt"]
            if confirmed["ok"]
            else confirmed["error"]["details"]["receipt"]
        )
        assert stored["receipt"] == receipt
        outcome = receipt["outcome"]
        if fault is None:
            assert confirmed["ok"], confirmed
            assert outcome["change"] == "applied" and outcome["persistence"] == "saved"
            assert outcome["verification"]["status"] == "passed"
            assert receipt["extensions"]["persistence_independently_reopened"] is True
            assert receipt["extensions"]["file_readback"]["engineering_complete"] is False
        else:
            assert not confirmed["ok"], confirmed
            assert outcome["verification"]["status"] in {"incomplete", "failed"}
            assert outcome["persistence"] == ("saved" if fault == "observer_saved" else "unknown")
        if fault in {"generation_failed", "generation_partial", "observer_partial"}:
            assert outcome["change"] == "partial"
            assert events == ["create", "generate"]
            native_result = receipt["extensions"]["result"]
            assert native_result["created_shape_uid"] == "301"
            assert (
                native_result["saved"] is False and native_result["persistence_verified"] is False
            )
            assert all(
                native_result["stages"][step] == "not_started"
                for step in ("save", "close", "reopen")
            )
        if fault in {"save_failed", "reopen_failed", "observer_saved", "observer_save_failed"}:
            assert outcome["change"] == "applied"
        if fault in {"save_failed", "observer_save_failed"}:
            assert events == ["create", "generate", "save"]
        if fault == "reopen_failed":
            native_result = receipt["extensions"]["result"]
            assert native_result["saved"] is True and native_result["persistence_verified"] is False
        if fault == "lost_reply_observer":
            assert outcome["execution"] == outcome["change"] == outcome["persistence"] == "unknown"
            assert receipt["extensions"]["result"] is None
        if fault in {"lost_reply", "lost_reply_observer"}:
            assert current_state()["plane_state"]["generated"]
            assert outcome["persistence"] == "unknown", (
                "Current copper is not a historical save witness"
            )
    (tmp_path / "public-copper-evidence.json").write_text(
        json.dumps(
            {
                "fault": fault,
                "preview": plan,
                "confirmed": confirmed,
                "repeated": repeat,
                "query": query,
                "events": events,
                "strict_reads": reads,
                "dispatches": dispatches,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("missing_effect", ["change", "persist", "session"])
def test_public_copper_scope_denial_never_dispatches(tmp_path, monkeypatch, missing_effect):
    backend, args, events, _, dispatches, _, _ = public_case(tmp_path, monkeypatch)
    scope_path = tmp_path / "scope.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    scope["effects"].remove(missing_effect)
    scope_path.write_text(json.dumps(scope), encoding="utf-8")
    with runtime(Adapter(backend)) as cli:
        result = cli.run([*args, "--dry-run"]).envelope
    assert not result["ok"] and result["error"]["code"] == "E_FORBIDDEN"
    assert not events and not dispatches


@pytest.mark.parametrize("drift", ["file", "net_uid", "assignment"])
def test_public_copper_input_drift_stops_before_dispatch(tmp_path, monkeypatch, drift):
    backend, args, events, _, dispatches, current_state, board = public_case(tmp_path, monkeypatch)
    with runtime(Adapter(backend)) as cli:
        plan = cli.run([*args, "--dry-run"]).envelope
        assert plan["ok"], plan
        if drift == "file":
            board.write_bytes(b"changed-since-preview")
        elif drift == "net_uid":
            current_state()["geometry"]["model"]["connectivity"]["nets"][0]["uid"] = "replacement"
        else:
            current_state()["plane_state"]["assignments"].append(
                {
                    "net": "B",
                    "net_uid": "12",
                    "layer": 1,
                    "plane_class": "Default",
                    "state": 2,
                    "route_border_as_shape": False,
                }
            )
        refused = cli.run([*args, "--confirm", plan["data"]["confirm_token"]]).envelope
    assert not refused["ok"] and refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert not events and not dispatches
    query = query_operation(plan["data"]["preview"]["operation_id"])
    (tmp_path / "public-copper-evidence.json").write_text(
        json.dumps(
            {
                "drift": drift,
                "preview": plan,
                "confirmed": refused,
                "query": query,
                "events": events,
                "dispatches": dispatches,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("unsupported", ["replace", "remove", "legacy_remove_flag"])
def test_public_copper_refuses_replacement_or_removal(tmp_path, monkeypatch, unsupported):
    backend, args, events, _, dispatches, _, _ = public_case(tmp_path, monkeypatch)
    if unsupported == "remove":
        args[2] = "remove"
        position = args.index("--margin")
        del args[position : position + 2]
    else:
        args.append("--remove" if unsupported == "legacy_remove_flag" else "--replace")
    with runtime(Adapter(backend)) as cli:
        refused = cli.run([*args, "--dry-run"]).envelope
    expected = "E_USAGE" if unsupported == "legacy_remove_flag" else "E_UNSUPPORTED"
    assert not refused["ok"] and refused["error"]["code"] == expected, refused
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert not events and not dispatches


@pytest.mark.parametrize("missing", ["net", "layer"])
def test_public_copper_requires_explicit_target(tmp_path, monkeypatch, missing):
    backend, args, events, _, dispatches, _, _ = public_case(tmp_path, monkeypatch)
    position = args.index("--" + missing)
    del args[position : position + 2]
    with runtime(Adapter(backend)) as cli:
        refused = cli.run([*args, "--dry-run"]).envelope
    assert not refused["ok"] and refused["error"]["code"] == "E_USAGE", refused
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert not events and not dispatches
