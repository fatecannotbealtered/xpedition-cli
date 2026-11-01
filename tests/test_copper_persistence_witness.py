"""Public receipts retain real execute-path witnesses from synthetic native objects."""

import json

import pytest
from test_copper_native import native_case
from test_copper_public import query_operation
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import board_copper as C
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize(
    "fault",
    [None, "reopen_plane_state_drift", "reopen_observe", "save_false", "reopen_rectangle_winding"],
)
def test_execute_witness_survives_public_receipt_and_new_process_query(
    tmp_path, monkeypatch, fault
):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    _scope(tmp_path, monkeypatch)
    app, params, events, calls = native_case(tmp_path, monkeypatch, fault)
    dispatches = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, options, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                assert options.get("plane_objects") is True and options.get("strict") is True
                return C.observe(app.ActiveDocument, A)
            assert method == "plane_pour", method
            if options.get("apply"):
                dispatches.append(method)
            try:
                return C.execute(options, object(), A)
            except A.AdapterError as error:
                raise CLIError(error.code, str(error), error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "copper",
        "pour",
        "--project",
        params["project"],
        "--net",
        params["net"],
        "--layer",
        str(params["layer"]),
        "--margin",
        str(params["margin"]),
    ]
    with runtime(Adapter(backend)) as cli:
        preview = cli.run([*args, "--dry-run"]).envelope
        assert preview["ok"], preview
        assert events == [] and dispatches == []
        token = preview["data"]["confirm_token"]
        operation = preview["data"]["preview"]["operation_id"]
        confirmed = cli.run([*args, "--confirm", token]).envelope
        dispatched_events, dispatched_calls = list(events), list(calls)
        repeated = cli.run([*args, "--confirm", token]).envelope
        assert not repeated["ok"]
        assert events == dispatched_events and calls == dispatched_calls
    query = query_operation(operation)
    assert events == dispatched_events and calls == dispatched_calls
    assert dispatches == ["plane_pour"]
    assert events.count("save") == 1
    assert events.count("close") == events.count("reopen") == (fault != "save_false")
    (tmp_path / "persistence-witness-evidence.json").write_text(
        json.dumps(
            {
                "fixture": "synthetic native execute objects; no CAD acceptance",
                "fault": fault,
                "preview": preview,
                "confirmed": confirmed,
                "repeated": repeated,
                "query": query,
                "events": events,
                "native_calls": calls,
                "dispatches": dispatches,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    receipt = (
        confirmed["data"]["receipt"]
        if confirmed["ok"]
        else confirmed["error"]["details"]["receipt"]
    )
    stored = query["envelope"]["data"]["operation_status"]["receipt"]
    assert stored == receipt
    result = stored["extensions"]["result"]
    before, after = result["state_before_save"], result["state_after"]
    assert before["plane_state"]["generated"][0]["hatch_distance_mm"] == 0.508
    assert {"state_before_save", "reopen_comparison"} <= set(result["_untrusted"])
    if fault in {None, "reopen_plane_state_drift", "reopen_rectangle_winding"}:
        comparison = result["reopen_comparison"]
        assert comparison["equal"] is (fault is None)
        assert comparison["before_sha256"] == C.digest(before)
        assert comparison["after_sha256"] == C.digest(after)
        assert comparison["geometry_equal"] is True
        assert comparison["plane_state_equal"] is (fault is None)
    else:
        assert "reopen_comparison" not in result and before == after
    if fault in {None, "reopen_rectangle_winding"}:
        assert confirmed["ok"], confirmed
        assert result["persistence_verified"] is True
        assert receipt["outcome"]["persistence"] == "saved"
        if fault == "reopen_rectangle_winding":
            assert before != after
            assert comparison["persistence_equivalent"] is True
            assert comparison["equivalence_basis"] == "target_rectangle_vertex_order"
    else:
        assert not confirmed["ok"]
        assert result["persistence_verified"] is False
        assert result["saved"] is (fault != "save_false")
        assert result["persistence_state"] == receipt["outcome"]["persistence"] == "unknown"
        expected_code = {
            "reopen_plane_state_drift": "E_VERIFY_INCOMPLETE",
            "reopen_observe": "E_NATIVE_FAILURE",
            "save_false": "E_SERVER",
        }[fault]
        assert result["post_write_error"]["code"] == expected_code
        if fault == "reopen_observe":
            assert result["stages"]["reopen"] == "returned"


def test_public_reference_declares_optional_persistence_witnesses():
    with runtime() as cli:
        envelope = cli.run(["reference", "--command", "pcb copper pour"]).envelope
    assert envelope["ok"], envelope
    schema = envelope["data"]["schemas"]["pcb_pour"]
    witnesses = {"state_before_save", "reopen_comparison"}
    assert witnesses <= set(schema["fields"])
    assert witnesses <= set(schema["optional_fields"])
    assert witnesses <= set(schema["untrusted_fields"])
    assert schema["state_before_save_shape"] == ["geometry", "plane_state"]
    assert set(schema["reopen_comparison_shape"]) == {
        "equal",
        "before_sha256",
        "after_sha256",
        "geometry_equal",
        "plane_state_equal",
        "persistence_equivalent",
        "equivalence_basis",
    }
    assert "strict" in schema["persistence_witness_boundary"]
    assert "unknown" in schema["persistence_witness_boundary"]
