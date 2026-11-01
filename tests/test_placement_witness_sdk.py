"""Public SDK and native observer wiring for fresh placement-only text witnesses."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from test_batch_driver import NativeBoundary
from test_driver_v2 import _scope
from test_placement_text_witness import text_board

from xpedition_cli.cli import common
from xpedition_cli.driver import Adapter, runtime


class WitnessBoundary(NativeBoundary):
    def __init__(self, board, mode):
        super().__init__(board, "success")
        self.witness_mode = mode
        self.driver.rows, self.driver.material, self.request = text_board()

    def invoke(self, method, params, **kwargs):
        if method == "cell_refresh_state":
            assert params["placement_text_witness"] is True
            assert params["strict"] is True and params["observe_only"] is True
            result = super().invoke(method, params, **kwargs)
            if "save" in self.driver.calls:
                state = result["geometry"]
                if self.witness_mode == "missing":
                    state.pop("text_native_witness")
                elif self.witness_mode == "duplicate":
                    state["text_native_witness"][1] = copy.deepcopy(state["text_native_witness"][0])
                elif self.witness_mode == "drift":
                    state["text_native_witness"][0]["stroke_array_mm"][1] += 1e-7
            return result
        return super().invoke(method, params, **kwargs)


@pytest.mark.parametrize("mode", ["success", "missing", "duplicate", "drift"])
def test_sdk_before_and_after_request_fresh_full_placement_witness_or_refuse(
    tmp_path, monkeypatch, mode
):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "independent-witness.pcb"
    board.write_bytes(b"offline synthetic native boundary")
    native = WitnessBoundary(board, mode)
    monkeypatch.setattr(common, "NativeBackend", lambda: native)
    source = tmp_path / "batch.json"
    source.write_text(json.dumps(native.request), encoding="utf-8")
    args = ["pcb", "placement", "move", "--project", str(board), "--file", str(source)]
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    reads = [params for method, params in native.calls if method == "cell_refresh_state"]
    assert len(reads) >= 2
    assert all(params["placement_text_witness"] is True for params in reads)
    if mode == "success":
        assert applied.envelope["ok"], applied.envelope
        receipt = applied.envelope["data"]["receipt"]
        for snapshot in (receipt["snapshot_before"], receipt["snapshot_after"]):
            state = snapshot["extensions"]["facts"]["cell_refresh"]["geometry"]
            assert len(state["text_native_witness"]) == 128
            assert len(state["components"]) == 127
            assert sum(len(c["pins"]) for c in state["components"]) == 467
        assert receipt["extensions"]["persistence_independently_reopened"] is True
    else:
        assert not applied.envelope["ok"]
        assert applied.envelope["error"]["code"] == "E_VERIFY_INCOMPLETE"
        receipt = applied.envelope["error"]["details"]["receipt"]
        assert receipt["outcome"]["verification"]["status"] == "incomplete"
        assert receipt["extensions"]["persistence_independently_reopened"] is False


def test_native_sdk_flag_uses_one_fresh_strict_capture_and_same_raw_collector(
    tmp_path, monkeypatch
):
    from xpedition_cli import cell_refresh, placement_text_witness
    from xpedition_cli import native_com_adapter as bridge

    calls = []
    board, doc, app = (
        tmp_path / "own.pcb",
        SimpleNamespace(FabricationLayerTexts=SimpleNamespace(Count=0)),
        object(),
    )
    _, first, _ = text_board()
    first.pop("text_native_witness")
    monkeypatch.setattr(
        bridge,
        "_import_com",
        lambda: (SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None), None),
    )
    monkeypatch.setattr(cell_refresh, "active", lambda *args, **kwargs: (None, board, app, doc))

    def full_capture(actual_doc, adapter, *, strict):
        assert actual_doc is doc and adapter is bridge and strict is True
        calls.append("full_capture")
        return copy.deepcopy(first)

    def raw_capture(actual_doc, adapter, state):
        assert actual_doc is doc and adapter is bridge
        calls.append("raw_capture")
        assert "text_native_witness" not in state
        return {**state, "text_native_witness": [{"fresh_call": len(calls)}]}

    monkeypatch.setattr(cell_refresh, "capture", full_capture)
    monkeypatch.setattr(placement_text_witness, "capture", raw_capture)
    monkeypatch.setattr(cell_refresh, "_readonly_identity", lambda *args: calls.append("identity"))
    params = {
        "project": str(board),
        "strict": True,
        "observe_only": True,
        "placement_text_witness": True,
    }
    a = bridge.dispatch("cell_refresh_state", params)
    b = bridge.dispatch("cell_refresh_state", params)
    assert calls == ["full_capture", "raw_capture", "identity"] * 2
    assert a["geometry"]["text_native_witness"] != b["geometry"]["text_native_witness"]
    calls.clear()
    normal = bridge.dispatch(
        "cell_refresh_state", {"project": str(board), "strict": True, "observe_only": True}
    )
    assert "text_native_witness" not in normal["geometry"]
    assert calls == ["full_capture", "identity"]
