"""Whole public SDK lifecycle with owned synthetic native objects, never CAD."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_driver_v2 import _scope, _worker_fixture
from test_routepass_guard import native_case

from xpedition_cli import board_routepass as R
from xpedition_cli import driver
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.errors import CLIError


def setup_case(tmp_path, monkeypatch, fault=None):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "state"))
    _scope(tmp_path, monkeypatch)
    app, params, events, _, net = native_case(tmp_path, monkeypatch, fault=fault)
    calls = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, request, **kwargs):
            calls.append((method, request.get("apply")))
            if method == "identity":
                return {
                    "application": {
                        "pid": 4242,
                        "document": {
                            "status": "observed",
                            "open": True,
                            "path": str(Path(params["project"]).parent / "PCB" / "owned.pcb"),
                        },
                    },
                    "adapter": _worker_fixture(),
                }
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "routepass_state":
                return R.observe_request(request, None, A)
            if method == "route_board":
                try:
                    return R.execute(request, None, A)
                except A.AdapterError as error:
                    raise CLIError(error.code, str(error), error.details) from error
            raise AssertionError("Unexpected native operation: " + method)

    backend = Backend()
    monkeypatch.setattr(driver, "NativeBackend", lambda: backend)
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "routing",
        "auto",
        "--mode",
        "bounded-native",
        "--project",
        params["project"],
        "--source-backup",
        params["source_backup"],
        "--nets",
        "A",
        "--layers",
        "1",
        "--dangerous",
    ]
    return NS(app=app, params=params, events=events, calls=calls, args=args, net=net)


def test_sdk_proves_multisegment_result_and_repeated_confirmation_does_not_dispatch(
    tmp_path, monkeypatch
):
    case = setup_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        assert planned.envelope["data"]["preview"]["can_apply"] is True
        token = planned.envelope["data"]["confirm_token"]
        done = cli.run([*case.args, "--confirm", token])
        assert done.exit_code == 0, done.envelope
        receipt = done.envelope["data"]["receipt"]
        assert receipt["extensions"]["file_readback"]["valid"] is True
        result = receipt["extensions"]["result"]
        assert result["persistence_verified"] is True
        assert len(result["verification"]["created_objects"]) == 2
        before_events = list(case.events)
        repeated = cli.run([*case.args, "--confirm", token])
        assert repeated.exit_code == 6, repeated.envelope
        assert case.events == before_events
        assert sum(e[0] == "Go" for e in case.events) == 1


@pytest.mark.parametrize("drift", ["source", "target_state"])
def test_sdk_rejects_drift_before_go(tmp_path, monkeypatch, drift):
    case = setup_case(tmp_path, monkeypatch)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        if drift == "source":
            from pathlib import Path

            Path(case.params["source_backup"]).write_bytes(b"changed source")
        else:
            case.app.ActiveDocument.state["texts"][0]["position"][0] += 1
        result = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert result.exit_code == 6, result.envelope
        assert not any(e[0] in {"Go", "Save", "Close", "Open"} for e in case.events)


def test_sdk_guard_failure_preserves_one_go_and_never_persists(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch, fault="protected")
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*case.args, "--dry-run"])
        assert planned.exit_code == 0, planned.envelope
        result = cli.run([*case.args, "--confirm", planned.envelope["data"]["confirm_token"]])
        assert result.exit_code != 0, result.envelope
        assert sum(e[0] == "Go" for e in case.events) == 1
        assert not any(e[0] in {"Save", "Close", "Open"} for e in case.events)


def test_legacy_route_confirmation_revision_refuses_before_native(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    with pytest.raises(driver.DriverError) as caught:
        driver.Adapter().revision({"extensions": {"command": "pcb routing auto"}})
    assert caught.value.code == "E_UNSUPPORTED"
    assert case.calls == []
    assert case.events == []


def test_only_scratch_receipt_is_transient_raw_material_still_binds_revision():
    import copy

    before = {
        "all_pin_pad_material": [{"point": [1.0, 2.0]}],
        "native_trace_material": [{"width": 0.254}],
        "native_mask_proof": {"temporary_mask_name": "Mask17"},
    }
    after = copy.deepcopy(before)
    after["native_mask_proof"]["temporary_mask_name"] = "Mask20"
    assert driver._stable(before) == driver._stable(after)
    after["native_trace_material"][0]["width"] = 0.3
    assert driver._stable(before) != driver._stable(after)
    after = copy.deepcopy(before)
    after["all_pin_pad_material"][0]["point"][0] += 0.000001
    assert driver._stable(before) != driver._stable(after)
