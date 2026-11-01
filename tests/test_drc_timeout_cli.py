"""Public preview/confirm through real worker timeout decoding; no worker runs."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest
from drc_fakes import application_identity, process_identity, write_review_evidence
from test_drc_engine import report_files
from test_driver_v2 import _scope, _worker_fixture
from test_refresh_review import prepare_project

from xpedition_cli.audit import config_dir
from xpedition_cli.backends import native_xpedition
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.operation_budget import profile_for
from xpedition_cli.session import read_state


@pytest.mark.parametrize("fault", ["worker_timeout", "nested_timeout", "nested_returned_progress"])
def test_public_timeout_never_dispatches_post_call_com_or_replay(
    tmp_path, monkeypatch, project, fault
):
    _scope(tmp_path, monkeypatch)
    scope_path = tmp_path / "scope.json"
    scope = json.loads(scope_path.read_text())
    scope["outputs"].append(str(config_dir() / "checks"))
    scope_path.write_text(json.dumps(scope))
    prepare_project(project)
    board = project.parent / "PCB/board.pcb"
    board.parent.mkdir(exist_ok=True)
    board.write_bytes(b"synthetic board; no CAD data")
    report_files(board)
    executable = tmp_path / "never-executed-worker.exe"
    executable.write_bytes(b"placeholder; subprocess.run is replaced")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(executable))
    monkeypatch.setattr(
        "xpedition_cli.driver._owned_process_identity",
        lambda *a: process_identity(board.parent.parent),
    )
    geometry = {"components": [], "model": {"layers": 4, "pads": []}, "stackup": []}
    dispatched = False
    calls, after_dispatch, packets = [], [], []
    original_profile = copy.deepcopy(profile_for("pcb checks run"))
    native_result = None
    if fault != "worker_timeout":
        native_result = {
            "pcb": str(board),
            "native_go_dispatched": True,
            "native_go_returned": fault == "nested_returned_progress",
            "native_stages": {
                "go": "returned" if fault == "nested_returned_progress" else "timed_out",
                "report_read": "started",
            },
            "board_save_acknowledged": True,
            "board_lifecycle": {
                "save_count": 1,
                "close_count": 1,
                "save_reply_received": True,
                "save_returned": True,
                "save_return_type": "bool",
                "save_acknowledged": True,
                "closed_observed": True,
            },
            "completion": {"status": "incomplete", "timed_out": True},
            "persistence_state": "partial",
            "persistence_verified": False,
        }

    def worker(argv, **kwargs):
        request = json.loads(kwargs["input"])
        packets.append(request)
        assert request["method"] == "batch_drc" and request["params"]["run"] is True
        if fault == "worker_timeout":
            # Exercise NativeBackend's actual TimeoutExpired -> E_TIMEOUT and
            # stale-session recording, then pcb.check's nested wrapper.
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "ok": False,
                    "schema_version": "1.0",
                    "error": {
                        "code": "E_TIMEOUT",
                        "message": "synthetic returned timeout packet",
                        "details": {"native_result": native_result},
                    },
                }
            ),
            "",
        )

    monkeypatch.setattr(native_xpedition.subprocess, "run", worker)
    transport = native_xpedition.NativeBackend()

    class Backend:
        def invoke(self, method, params, **kwargs):
            nonlocal dispatched
            calls.append(method)
            if dispatched:
                after_dispatch.append(method)
            if method == "identity":
                return {"application": application_identity(board), "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                return {"geometry": copy.deepcopy(geometry)}
            assert method == "batch_drc" and params["run"] is True
            dispatched = True
            return transport.invoke(method, params, **kwargs)

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = ["pcb", "checks", "run", "--project", str(project)]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        assert not dispatched and not packets
        token = preview.envelope["data"]["confirm_token"]
        failed = cli.run([*args, "--confirm", token, "--quiet"])
        assert failed.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", failed.envelope
        assert after_dispatch == [], {
            "post_dispatch_methods": after_dispatch,
            "error_message": failed.envelope["error"]["message"],
            "worker_packets": len(packets),
        }
        receipt = failed.envelope["error"]["details"]["receipt"]
        assert after_dispatch == [], "Every post-dispatch native request is forbidden"
        assert receipt["extensions"]["result"] == native_result
        assert receipt["extensions"]["save_acknowledged"] is (native_result is not None)
        assert receipt["outcome"]["execution"] == "unknown"
        assert receipt["outcome"]["change"] == receipt["outcome"]["persistence"] == "unknown"
        assert receipt["outcome"]["verification"]["status"] == "incomplete"
        assert receipt["extensions"]["independent_readback"]["native_request_dispatched"] is False
        assert receipt["extensions"]["independent_readback"]["status"] == "unobserved"
        assert receipt["extensions"]["readback_error"] is None  # No attempted observation.
        assert receipt["extensions"]["independent_readback_completed"] is False
        if native_result is None:
            assert receipt["snapshot_after"] is None
            assert read_state()["state"] == "stale"
            assert read_state()["timed_out_method"] == "batch_drc"
        else:
            nested = receipt["extensions"]["handler_error"]["details"]["cause"]
            assert nested["code"] == "E_TIMEOUT"
            assert nested["details"]["native_result"] == native_result
        operation_id = failed.envelope["error"]["details"]["operation_id"]
        stored = copy.deepcopy(cli._store.get(operation_id))
        assert stored["status"] == "unknown" and stored["resolution"] is None
        shown = cli.run(["operation", "show", "--id", operation_id, "--quiet"])
        assert shown.envelope["data"]["operation_status"]["receipt"] == stored["receipt"]
        replay = cli.run([*args, "--confirm", token, "--quiet"])
        assert not replay.envelope["ok"]
        assert cli._store.get(operation_id)["status"] == "unknown"
        assert cli._store.get(operation_id)["receipt"] == stored["receipt"]
        assert after_dispatch == []
        assert len(packets) == 1 and calls.count("batch_drc") == 1
        assert profile_for("pcb checks run") == original_profile
        write_review_evidence(
            "timeout-cli-" + fault,
            {
                "kind": "offline_public_cli_worker_transport_not_native_execution",
                "fault": fault,
                "receipt": receipt,
                "status": stored["status"],
                "post_dispatch_methods": after_dispatch,
                "worker_packets": len(packets),
                "replay_rejected": True,
                "lookup_preserved_receipt": True,
                "budget_profile_unchanged": True,
            },
            tmp_path,
        )
