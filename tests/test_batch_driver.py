"""Real public DriverCLI and handler; only the native JSON boundary is doubled."""

from __future__ import annotations

import copy
import json

import pytest
from test_batch_material_validation import RepresentativeDriver
from test_driver_v2 import _scope, _worker_fixture
from test_placement import guarded_plan

from xpedition_cli.cli import common
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.placement import execute_placement


class NativeBoundary:
    def __init__(self, board, mode):
        self.board = board
        self.mode = mode
        self.driver = RepresentativeDriver()
        self.calls = []

    def require_implemented(self):
        pass

    def invoke(self, method, params, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "identity":
            return {
                "application": {
                    "pid": 4141,
                    "document": {"open": True, "path": str(self.board)},
                    "fixture": True,
                },
                "adapter": _worker_fixture(),
            }
        if method == "snapshot":
            return {
                "components": copy.deepcopy(self.driver.material["components"]),
                "pcb": {
                    "components": copy.deepcopy(self.driver.material["components"]),
                    "tracks": copy.deepcopy(self.driver.material["model"]["traces"]),
                    "vias": [],
                    "nets": [],
                },
                "fixture": True,
            }
        if method == "cell_refresh_state":
            assert params["strict"] is True and params["pid"] == 4141
            state = self.driver.capture_state()
            if self.mode == "independent_drift" and "save" in self.driver.calls:
                state["model"]["traces"][0]["net"] = "INDEPENDENT_ACTUAL_CHANGE"
            return {"board": str(self.board), "geometry": state}
        assert method == "placement_batch", method
        if not params["apply"]:
            answer = guarded_plan(params["request"], self.driver)
        else:
            answer = execute_placement(params["request"], params["state_digest"], self.driver)
            answer["lifecycle"].update(
                pid_before=4141,
                pid_after=4141,
                board=str(self.board),
                reopened_path=str(self.board),
            )
            if self.mode == "forged_success":
                answer["geometry_after"]["components"][-1]["footprint"] = "COUNTERFEIT"
        return {**answer, "pcb": str(self.board), "prompts": []}


@pytest.mark.parametrize("mode", ["success", "independent_drift", "forged_success", "noop"])
def test_public_batch_full_sdk_observes_all_467_pins_and_reopens_or_refuses_at_verification(
    tmp_path, monkeypatch, mode
):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "arbitrary-holdout-829.pcb"
    board.write_bytes(b"offline synthetic native boundary")
    native = NativeBoundary(board, mode)
    monkeypatch.setattr(common, "NativeBackend", lambda: native)
    selection = [r["refdes"] for r in native.driver.rows[:3]]
    task = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": selection,
        "steps": [{"op": "translate", "dx": 0 if mode == "noop" else 0.25, "dy": 0}],
    }
    source = tmp_path / "batch.json"
    source.write_text(json.dumps(task), encoding="utf-8")
    args = ["pcb", "placement", "move", "--project", str(board), "--file", str(source)]
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        assert not native.driver.calls
        token = preview.envelope["data"]["confirm_token"]
        applied = cli.run([*args, "--confirm", token])
        replay = cli.run([*args, "--confirm", token])
    assert replay.envelope["ok"] is False
    assert (
        sum(method == "placement_batch" and params["apply"] for method, params in native.calls) == 1
    )
    if mode in {"independent_drift", "forged_success"}:
        assert applied.envelope["ok"] is False, applied.envelope
        assert applied.envelope["error"]["code"] == "E_VERIFY_INCOMPLETE"
        receipt = applied.envelope["error"]["details"]["receipt"]
        assert receipt["outcome"]["verification"]["status"] == "incomplete"
    else:
        assert applied.envelope["ok"], applied.envelope
        receipt = applied.envelope["data"]["receipt"]
        facts = receipt["snapshot_after"]["extensions"]["facts"]
        assert (
            sum(len(row["pins"]) for row in facts["cell_refresh"]["geometry"]["components"]) == 467
        )
        assert receipt["outcome"]["verification"]["status"] == "passed"
        if mode == "noop":
            assert receipt["outcome"]["persistence"] == "unchanged"
            assert receipt["extensions"]["persistence_independently_reopened"] is False
            assert "save" not in native.driver.calls
        else:
            assert receipt["outcome"]["persistence"] == "saved"
            assert receipt["extensions"]["persistence_independently_reopened"] is True
            assert native.driver.calls.count("save") == 1
