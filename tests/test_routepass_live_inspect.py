"""Public attach-only material read with synthetic native inputs; no CAD proof."""

import copy
from types import SimpleNamespace as NS

import pytest
from test_driver_v2 import _scope
from test_native_pad_mask import evaluated

from xpedition_cli import (
    board_labels,
    board_routepass,
    cell_refresh,
    copper_ties,
    native_identity,
    session,
)
from xpedition_cli import native_com_adapter as A
from xpedition_cli.backends.native_xpedition import _attached_geometry_read, _refuse_a_stale_session
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.operation_budget import profile_for

LIVE_READ_LIMIT = int(profile_for("pcb inspect")["native_call_seconds"]["board_info"])


@pytest.mark.parametrize("read_timeout", [180, 600, LIVE_READ_LIMIT])
def test_public_one_net_one_layer_read_stays_on_existing_observe_only_method(
    tmp_path, monkeypatch, read_timeout
):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "EXAMPLE_BOARD.pcb"
    board.write_bytes(b"synthetic board")
    calls = []

    class Backend:
        read_timeout_seconds = read_timeout

        def invoke(self, method, params, **kwargs):
            calls.append((method, params, kwargs))
            assert method == "board_info"
            return {
                "pcb": str(board),
                "observation": {"engineering_write_dispatched": False, "persistence": "unknown"},
            }

    monkeypatch.setattr(pcb, "reader", lambda _: Backend())
    with runtime(Adapter(Backend()), state_dir=tmp_path / "original-state") as cli:
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
                "A_IN",
                "--layers",
                "1",
                "--timeout",
                str(read_timeout),
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
                "routepass_request": {"net": "A_IN", "layer": 1},
            },
            {"timeout_seconds": read_timeout},
        )
    ]
    assert _attached_geometry_read(calls[0][0], calls[0][1])


@pytest.mark.parametrize(
    "extra",
    [
        ["--nets", "A,B", "--layers", "1"],
        ["--nets", "A", "--layers", "1,2"],
        ["--nets", "A"],
        ["--layers", "1"],
        ["--nets", "A", "--layers", "1", "--timeout", str(LIVE_READ_LIMIT + 1)],
    ],
)
def test_public_invalid_material_scope_never_invokes_native(tmp_path, monkeypatch, extra):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "EXAMPLE_BOARD.pcb"
    board.write_bytes(b"synthetic")

    class Backend:
        read_timeout_seconds = LIVE_READ_LIMIT + 1 if "--timeout" in extra else 120

        def invoke(self, *_args, **_kwargs):
            pytest.fail("invalid read must stop before native dispatch")

    monkeypatch.setattr(pcb, "reader", lambda _: Backend())
    with runtime(Adapter(Backend()), state_dir=tmp_path / "original-state") as cli:
        result = cli.run(
            ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "42", *extra]
        )
    assert result.envelope["error"]["code"] == "E_VALIDATION", result.envelope


def native_case(tmp_path, monkeypatch, fault=None):
    _, state, request, _, _ = evaluated()
    state["texts"] = []
    board = tmp_path / "EXAMPLE_BOARD.pcb"
    board.write_bytes(b"synthetic")
    doc = NS(
        FullName=str(board),
        CurrentUnit=4 if fault == "unit" else 2,
        Save=lambda: pytest.fail("read cannot Save"),
        Close=lambda *_: pytest.fail("read cannot Close"),
    )
    app = NS(ActiveDocument=doc)
    calls, identity_reads = [], []

    def application(_client, **kwargs):
        assert kwargs == {"attach_only": True, "quiet": False}
        return app

    def identity(_app, _board):
        identity_reads.append(1)
        return {
            "generation": {
                "pid": 42,
                "birth": 2 if fault == "generation" and len(identity_reads) > 1 else 1,
            },
            "board": str(board),
        }

    def capture(*_args, **_kwargs):
        calls.append("capture")
        geometry = copy.deepcopy(state["geometry"])
        if fault == "geometry_drift" and calls.count("capture") > 1:
            geometry["changed"] = True
        return geometry

    def observe(actual_doc, _adapter, actual_request, **_kwargs):
        assert actual_doc is doc and actual_request == request
        calls.append("complete_material_read")
        if fault == "getter":
            raise A.AdapterError(
                "E_NATIVE_FAILURE",
                "synthetic raw getter failed",
                {"failed_member": "Trace.Extrema"},
            )
        result = copy.deepcopy(state)
        if fault == "raw_material":
            result["all_pin_pad_material"][0]["material"]["complete"] = False
        if fault == "state_geometry":
            result["geometry"]["different"] = True
        return result

    monkeypatch.setattr(A, "_application", application)
    monkeypatch.setattr(A, "_licensed_document", lambda _app: doc)
    monkeypatch.setattr(A, "_open_layout_document", lambda *_: pytest.fail("read cannot Open"))
    monkeypatch.setattr(
        native_identity, "application_identity", lambda *_: {"pid": 99 if fault == "pid" else 42}
    )
    monkeypatch.setattr(board_labels, "_identity", identity)
    monkeypatch.setattr(cell_refresh, "capture", capture)
    monkeypatch.setattr(copper_ties, "observe", lambda *_: {"complete": True, "items": []})
    monkeypatch.setattr(board_routepass, "observe", observe)
    monkeypatch.setattr(
        board_labels,
        "observe",
        lambda *_, **_kwargs: {
            "geometry": copy.deepcopy(state["geometry"]),
            "texts": copy.deepcopy(state.get("texts", [])),
        },
    )
    monkeypatch.setattr(
        board_routepass, "execute", lambda *_: pytest.fail("read cannot execute pass")
    )
    return board, state, request, calls


def test_native_complete_material_state_bracketed_by_two_geometry_reads(tmp_path, monkeypatch):
    board, state, request, calls = native_case(tmp_path, monkeypatch)
    result = A._board_observation(
        {"project": str(board), "pid": 42, "routepass_request": request}, object()
    )
    observed = result["observation"]
    assert calls == ["capture", "complete_material_read", "capture"]
    assert observed["native_routepass_state"] == state
    assert observed["native_routepass_request"] == request
    assert observed["native_routepass_evidence"]["valid"]
    assert observed["engineering_write_dispatched"] is False
    assert observed["persistence"] == "unknown"
    assert "one complete raw/material read" in observed["native_routepass_consistency"]


def test_native_continuation_read_preserves_complete_three_groups_without_claiming_completion(
    tmp_path, monkeypatch
):
    from test_routepass_continuation import continuation_case

    board, state, request, calls = native_case(tmp_path, monkeypatch)
    plan, _ = continuation_case()
    state.clear()
    state.update(copy.deepcopy(plan["before"]))
    request.clear()
    request.update(copy.deepcopy(plan["request"]))
    result = A._board_observation(
        {"project": str(board), "pid": 42, "routepass_request": request}, object()
    )
    observed = result["observation"]
    components = observed["native_routepass_components"]
    assert components["observation_valid"] is True
    assert components["completion_valid"] is False
    assert len(components["components"]) == 3
    assert observed["native_routepass_state"] == state
    assert observed["native_routepass_evidence"]["valid"] is True
    assert calls == ["capture", "complete_material_read", "capture"]
    assert observed["engineering_write_dispatched"] is False


def test_native_continuation_read_rejects_unproved_component_observation_but_retains_raw(
    tmp_path, monkeypatch
):
    from test_routepass_continuation import continuation_case

    from xpedition_cli import native_pad_mask

    board, state, request, calls = native_case(tmp_path, monkeypatch)
    plan, _ = continuation_case()
    state.clear()
    state.update(copy.deepcopy(plan["before"]))
    request.clear()
    request.update(copy.deepcopy(plan["request"]))
    unavailable = {
        "observation_valid": False,
        "completion_valid": False,
        "reason": "unproved pad bridge",
    }
    monkeypatch.setattr(native_pad_mask, "observe_material_components", lambda *_: unavailable)
    with pytest.raises(A.AdapterError) as caught:
        A._board_observation(
            {"project": str(board), "pid": 42, "routepass_request": request}, object()
        )
    assert caught.value.code == "E_NATIVE_FAILURE"
    assert caught.value.details["native_routepass_components"] == unavailable
    assert caught.value.details["native_routepass_state"] == state
    assert calls == ["capture", "complete_material_read"]


@pytest.mark.parametrize(
    "fault",
    ["pid", "unit", "generation", "geometry_drift", "raw_material", "getter", "state_geometry"],
)
def test_incomplete_or_drifted_native_read_preserves_failure_without_lifecycle(
    tmp_path, monkeypatch, fault
):
    board, _, request, calls = native_case(tmp_path, monkeypatch, fault)
    with pytest.raises(A.AdapterError) as caught:
        A._board_observation(
            {"project": str(board), "pid": 42, "routepass_request": request}, object()
        )
    details = caught.value.details
    assert details["write_attempted"] is False
    if fault in {"pid", "unit"}:
        assert not calls
    elif fault == "getter":
        assert details["failed_member"] == "Trace.Extrema"
        assert details["native_routepass_state"] is None
    else:
        assert isinstance(details["native_routepass_state"], dict)
        if fault == "raw_material":
            assert caught.value.code == "E_NATIVE_FAILURE"
            assert details["native_routepass_evidence"]["valid"] is False


def test_public_incomplete_material_query_preserves_partial_evidence_not_write_receipt(
    tmp_path, monkeypatch
):
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    board, _, request, calls = native_case(tmp_path, monkeypatch, "raw_material")

    class Backend:
        read_timeout_seconds = 180

        def invoke(self, method, params, **kwargs):
            assert method == "board_info" and params["routepass_request"] == request
            try:
                return A._board_observation(params, object())
            except A.AdapterError as error:
                raise CLIError(error.code, error.message, error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "reader", lambda _: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "original-state") as cli:
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
                request["net"],
                "--layers",
                str(request["layer"]),
            ]
        )
    assert result.exit_code == 1
    error = result.envelope["error"]
    assert error["code"] == "E_NATIVE_FAILURE", result.envelope
    assert error["details"]["native_query_only"] is True
    assert error["details"]["engineering_write_dispatched"] is False
    assert error["details"]["write_attempted"] is False
    assert error["details"]["native_routepass_state"] is not None
    assert error["details"]["native_routepass_evidence"]["valid"] is False
    assert error["details"]["next_action"].startswith("This read is incomplete.")
    assert "earlier actions" in error["details"]["next_action"]
    assert "Nothing lasting changed" not in error["details"]["next_action"]
    assert "receipt" not in error["details"]
    assert calls == ["capture", "complete_material_read"]


def test_public_unproved_component_read_preserves_actual_adapter_failure_details(
    tmp_path, monkeypatch
):
    from test_routepass_continuation import continuation_case

    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    board, state, request, calls = native_case(tmp_path, monkeypatch)
    plan, material_state = continuation_case(fault="unproved_pad_arc")
    state.clear()
    state.update(material_state)
    request.clear()
    request.update({key: value for key, value in plan["request"].items() if key != "effort"})
    # The fixture runs native-shaped mask operations. Do not replace the
    # component observer or supply a precomputed observation_valid=False.
    writes = dict.fromkeys(("Save", "Close", "Open", "Go", "PutTrace"), 0)

    def forbidden(name):
        def call(*_args, **_kwargs):
            writes[name] += 1
            pytest.fail(f"read cannot {name}")

        return call

    app = A._application(object(), attach_only=True, quiet=False)
    app.ActiveDocument.Save = forbidden("Save")
    app.ActiveDocument.Close = forbidden("Close")
    app.ActiveDocument.PutTrace = forbidden("PutTrace")
    monkeypatch.setattr(A, "_open_layout_document", forbidden("Open"))
    monkeypatch.setattr(board_routepass, "execute", forbidden("Go"))
    adapter_failures = []

    class Backend:
        read_timeout_seconds = 180

        def invoke(self, method, params, **kwargs):
            assert method == "board_info" and params["routepass_request"] == request
            assert params["start"] is False and params["pid"] == 42
            try:
                return A._board_observation(params, object())
            except A.AdapterError as error:
                adapter_failures.append(error)
                raise CLIError(error.code, error.message, error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "reader", lambda _: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "original-state") as cli:
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
                "--continue-existing",
            ]
        )
    assert len(adapter_failures) == 1
    details = adapter_failures[0].details
    assert details["native_routepass_evidence"]["valid"] is True
    component_observation = details["native_routepass_components"]
    assert component_observation["observation_valid"] is False
    assert component_observation["reason"] == "complete connected owned-region observation for p4"
    # Actual native-shaped adapter operations preserve the complete raw masks,
    # then decline an arbitrary arced pad's unproved material topology. A known
    # zero-area gap is instead a valid incomplete graph and is no read failure.
    region = next(
        row for row in state["native_mask_proof"]["target_pad_regions"] if row["pin_uid"] == "p4"
    )
    assert not region["complete"]
    assert "Arced pad topology" in region["reason"]
    assert component_observation["nodes"]
    assert details["native_routepass_state"] == state
    assert details["native_routepass_request"] == request
    assert details["native_query_only"] is True
    assert details["write_attempted"] is False
    assert details["engineering_write_dispatched"] is False
    assert details["persistence"] == "unknown"
    assert "receipt" not in details
    assert writes == dict.fromkeys(writes, 0)
    assert calls == ["capture", "complete_material_read"]
    assert result.exit_code == 1, result.envelope
    assert result.envelope["ok"] is False
    assert result.envelope["error"]["code"] == "E_NATIVE_FAILURE", result.envelope
    assert result.envelope["error"]["retryable"] is False
    public_details = result.envelope["error"]["details"]
    assert {key: public_details[key] for key in details} == details
    assert "receipt" not in public_details
    assert public_details["next_action"].startswith("This read is incomplete.")


@pytest.mark.parametrize(
    "code,query_flag,existing_action,use_query_advice",
    [
        ("E_NATIVE_FAILURE", True, None, True),
        ("E_NATIVE_FAILURE", False, None, False),
        ("E_NATIVE_FAILURE", "missing", None, False),
        ("E_NATIVE_FAILURE", 1, None, False),
        ("E_NATIVE_FAILURE", True, "Inspect the original GUI task without replay.", False),
        ("E_CONFLICT", True, None, False),
    ],
)
def test_query_recovery_advice_does_not_erase_earlier_copper_or_persistence_facts(
    tmp_path, monkeypatch, code, query_flag, existing_action, use_query_advice
):
    from xpedition_cli._vendor.tool_driver_sdk.runtime import NEXT_ACTION
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    board = tmp_path / "EXAMPLE_BOARD.pcb"
    board.write_bytes(b"synthetic query target")
    native_details = {
        "write_attempted": False,
        "engineering_write_dispatched": False,
        "persistence": "unknown",
        "native_routepass_state": {
            "routing_objects": [
                {"kind": "trace", "uid": "1762", "net": "GND", "geometry": {"layer": 2}}
            ]
        },
        "native_routepass_evidence": {"valid": False, "reason": "target layer unsupported"},
    }
    if query_flag != "missing":
        native_details["native_query_only"] = query_flag
    if existing_action:
        native_details["next_action"] = existing_action
    frozen = copy.deepcopy(native_details)
    calls = []

    class Backend:
        read_timeout_seconds = 180

        def invoke(self, method, params, **_kwargs):
            calls.append((method, params))
            assert method == "board_info" and params.get("start") is False
            raise CLIError(code, "Actual native read is incomplete", native_details)

    backend = Backend()
    monkeypatch.setattr(pcb, "reader", lambda _: backend)
    with runtime(Adapter(backend), state_dir=tmp_path / "isolated-test-state") as cli:
        result = cli.run(
            ["pcb", "inspect", "--project", str(board), "--live-geometry", "--pid", "42"]
        )
    assert result.envelope["ok"] is False
    error = result.envelope["error"]
    assert error["code"] == code
    details = error["details"]
    assert {key: details[key] for key in frozen} == frozen
    assert native_details == frozen
    assert "receipt" not in details
    assert details["persistence"] == "unknown"
    assert details["native_routepass_state"]["routing_objects"][0]["geometry"]["layer"] == 2
    assert len(calls) == 1
    if use_query_advice:
        assert details["next_action"].startswith("This read is incomplete.")
        assert "Nothing lasting changed" not in details["next_action"]
        assert "plan again" not in details["next_action"]
    else:
        assert details["next_action"] == (
            existing_action
            or NEXT_ACTION.get(
                code, "Read the message and details, then decide; do not resend blindly."
            )
        )


def test_unknown_session_record_is_unchanged_by_new_attached_read_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XPEDITION_OBSERVE_ONLY", "1")
    session.record_native_timeout("route_board")
    before = session.read_state()
    params = {
        "geometry": True,
        "start": False,
        "pid": 42,
        "routepass_request": {"net": "A_IN", "layer": 1},
    }
    assert _attached_geometry_read("board_info", params)
    _refuse_a_stale_session("board_info", params)
    assert session.read_state() == before


def test_existing_hazard_read_is_attached_and_not_a_new_checker_run(tmp_path, monkeypatch):
    board, _, _, calls = native_case(tmp_path, monkeypatch)
    monkeypatch.setattr(
        native_identity,
        "application_identity",
        lambda *_: {"pid": 42, "type_library": {"major": 151}},
    )
    hazard_calls = []

    def hazards(doc, client, kind):
        hazard_calls.append(kind)
        return [{"type": 67, "kind": "67", "object_details": []}]

    monkeypatch.setattr(A, "_hazards", hazards)
    result = A._board_observation(
        {"project": str(board), "pid": 42, "geometry": True, "drc_hazards": True}, object()
    )
    proof = result["observation"]["drc_hazards_observation"]
    assert hazard_calls == [A.HAZARDS_BATCH]
    assert calls == ["capture", "capture"]
    assert proof["count"] == 1 and proof["collection_complete"] is True
    assert proof["hazards"][0]["kind"] == "Proximity"
    assert proof["freshness"] == "existing_current_document"
    assert proof["engine_executed"] is False and proof["passes"] is False
    assert proof["save_close_open_dispatched"] is False


def test_hazard_flag_cannot_start_an_ordinary_board_info_read(tmp_path, monkeypatch):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic")
    monkeypatch.setattr(pcb, "reader", lambda _: pytest.fail("scope refused before reader"))
    with pytest.raises(Exception) as caught:
        pcb.info({"project": str(board), "drc_hazards": True})
    assert caught.value.code == "E_VALIDATION"
    with pytest.raises(A.AdapterError) as caught_native:
        A._board_info({"project": str(board), "drc_hazards": True}, object())
    assert caught_native.value.code == "E_VALIDATION"
    assert caught_native.value.details["write_attempted"] is False
