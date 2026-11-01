"""Public SDK view receipts with synthetic native state, not native acceptance."""

import copy
import hashlib
import json

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import board_view as V
from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "click_only",
        "unreadable",
        "geometry",
        "rules",
        "png",
        "locked",
        "source_drift",
        "capture_missing",
        "capture_pixels",
        "capture_pid",
        "capture_hwnd",
        "capture_space",
        "capture_failure",
    ],
)
def test_view_plan_confirm_and_independent_observation(tmp_path, monkeypatch, fault):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "a.pcb"
    board.write_bytes(b"synthetic")
    config = tmp_path / "Config"
    config.mkdir()
    source = config / "All On.dcs"
    source.write_text("synthetic scheme")
    output = tmp_path / "capture.png"
    state = {
        "pcb": str(board),
        "application": {"pid": 4242},
        "model": {"traces": [{"net": "A"}]},
        "rule_observation": {"files": {"synthetic.drc": "frozen"}},
        "display": {
            "scheme": "Loc: All On",
            "viewport_mm": [0, 0, 10, 10],
            "complete": True,
            "visible_objects": [
                {"uid": "1", "kind": "component", "refdes": "R1", "visible": False},
                {"uid": "2", "kind": "pin", "refdes": "R1", "pin": "1", "visible": True},
            ],
        },
        "schemes": ["Loc: All On"],
        "layout_reminders": [],
    }
    writes = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "board_view_state":
                return {**copy.deepcopy(state), "scheme_plan": V.scheme_plan(board, 4, params, A)}
            assert method == "show_board"
            writes.append(True)
            if fault == "locked":
                raise CLIError("E_HUMAN_REQUIRED", "locked", {"write_attempted": False})
            if fault == "geometry":
                state["model"]["traces"] = []
            if fault == "rules":
                state["rule_observation"]["files"]["synthetic.drc"] = "changed"
            if fault == "click_only":
                state["display"]["scheme"] = "Loc: Assembly"
            if fault == "unreadable":
                state["display"]["complete"] = False
            from PIL import Image, PngImagePlugin

            capture = {
                "window_hwnd": 12,
                "window_pid": 4242,
                "bounds_px": [10, 20, 12, 22],
                "coordinate_space": "physical_pixels",
                "bounds_verified": True,
                "method": "gdi_physical_frame",
                "dpi": 120,
            }
            if fault == "capture_missing":
                capture = {}
            if fault == "capture_pixels":
                capture["bounds_px"] = [10, 20, 13, 22]
            if fault == "capture_pid":
                capture["window_pid"] = 4343
            if fault == "capture_hwnd":
                capture["window_hwnd"] = 13
            if fault == "capture_space":
                capture["coordinate_space"] = "dpi_virtualized"
            binding = {
                "generation_id": "old" if fault == "png" else params["artifact_generation"],
                "pcb": str(board),
                "project": str(board),
                "pid": 4242,
                "scheme": "Loc: All On",
                "viewport_mm": [0, 0, 10, 10],
                "source_geometry_sha256": V.digest(state["model"]),
                "capture": capture,
            }
            meta = PngImagePlugin.PngInfo()
            meta.add_text("xpedition", json.dumps(binding))
            if fault != "capture_failure":
                Image.new("RGB", (2, 2), "white").save(output, pnginfo=meta)
            return {
                "pcb": str(board),
                "scheme": "Loc: All On",
                "scheme_applied": True,
                "foreground": True,
                "identity_verified": True,
                "window_hwnd": 12,
                "locked": False,
                "view": {"window": [0, 0, 10, 10]},
                "screenshot": (
                    {"valid_context": False, "complete": False, "error": {"code": "E_IO"}}
                    if fault == "capture_failure"
                    else {"valid_context": True, "width": 2, "height": 2, **capture}
                ),
                **(
                    {"artifact": {"sha256": hashlib.sha256(output.read_bytes()).hexdigest()}}
                    if output.exists()
                    else {}
                ),
            }

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    from xpedition_cli.cli import common

    monkeypatch.setattr(common, "reader", lambda options: backend)
    args = [
        "pcb",
        "view",
        "--project",
        str(board),
        "--scheme",
        "Loc: All On",
        "--output",
        str(output),
    ]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        assert not writes and not output.exists()
        legacy = plan.envelope["data"]["preview"]["extensions"]["legacy_preview"]
        assert legacy["view_plan"]["scheme_plan"]["source"]["path"] == str(source)
        if fault == "source_drift":
            source.write_text("changed")
        reply = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
        assert reply.envelope["ok"] is (fault is None), reply.envelope
        op = plan.envelope["data"]["preview"]["operation_id"]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        queried = cli.run(["operation", "show", "--id", op])
        assert queried.envelope["ok"]
    if fault == "capture_failure":
        receipt = queried.envelope["data"]["operation_status"]["receipt"]
        assert receipt["outcome"]["execution"] == "completed"
        assert receipt["extensions"]["result"]["scheme_applied"] is True
    if fault == "source_drift":
        assert not writes and not output.exists()
