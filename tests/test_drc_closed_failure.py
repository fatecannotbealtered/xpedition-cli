"""Actual fake Save/Close state transitions through public preview/confirm.

The COM engine, document and process identities are synthetic. Real report
parsing, native lifecycle accounting, SDK planning and journaling are exercised.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from drc_fakes import application_identity, process_identity
from test_drc_engine import report_files
from test_driver_v2 import _scope, _worker_fixture
from test_refresh_review import prepare_project

from xpedition_cli import cell_refresh, native_identity
from xpedition_cli import drc_engine as drc
from xpedition_cli import native_com_adapter as bridge
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "other_board",
        "pid_reuse",
        "worker_drift",
        "log_access",
        "save_reply_lost",
        "report_replaced",
    ],
)
def test_closed_board_configuration_refusal_keeps_bound_evidence_and_unknown(
    tmp_path, monkeypatch, project, fault
):
    _scope(tmp_path, monkeypatch)
    from xpedition_cli.audit import config_dir

    scope_path = tmp_path / "scope.json"
    scope = json.loads(scope_path.read_text())
    scope["outputs"].append(str(config_dir() / "checks"))
    scope_path.write_text(json.dumps(scope))
    prepare_project(project)
    board = project.parent / "PCB/board.pcb"
    board.parent.mkdir(exist_ok=True)
    board.write_bytes(b"synthetic board; no engineering model")
    paths = report_files(board)
    inputs = {}
    for kind in ("rules", "elements"):
        path = tmp_path / ("input-" + kind)
        path.write_bytes(paths["effective_" + kind].read_bytes())
        if kind == "rules":
            path.write_text(
                '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.BASE_UNITS 0\n'
                '.MINIMUMANNULARRING YES\n.MINIMUMANNULARRINGVALUE "124460"\n'
            )
        inputs[kind] = str(path)
    methods, events = [], []
    geometry = {"components": [], "model": {"layers": 4, "pads": []}, "stackup": []}
    app = SimpleNamespace(ActiveDocument=None)

    class Document:
        FullName = str(board)

        def Save(self):
            events.append("Save")
            if fault == "save_reply_lost":
                raise RuntimeError("synthetic Save reply lost")
            return True

        def Close(self, save):
            assert save is True
            events.append("Close")
            app.ActiveDocument = None
            return True

    app.ActiveDocument = Document()

    class Engine:
        Errors = []

        def Go(self):
            assert app.ActiveDocument is None, "Engine must see the actual fake board closed"
            events.append("Go")
            report_files(board)
            paths["effective_rules"].write_bytes(
                Path(inputs["rules"]).read_bytes().replace(b"124460", b"4.9")
            )
            paths["effective_elements"].write_bytes(Path(inputs["elements"]).read_bytes())
            paths["report"].write_text(
                "DRC\n===\n环形距离: 0th\nTotal DRC Hazards Found : 1\n", encoding="utf-8"
            )
            for effect in drc.planned_side_effects(board)[5:]:
                path = Path(effect["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic lifecycle log\n")
            if fault == "other_board":
                app.ActiveDocument = SimpleNamespace(FullName=str(tmp_path / "foreign.pcb"))

    def actual_identity():
        value = application_identity(board, opened=app.ActiveDocument is not None)
        if app.ActiveDocument is not None:
            value["document"]["path"] = app.ActiveDocument.FullName
        return value

    monkeypatch.setattr(cell_refresh, "capture", lambda *a: copy.deepcopy(geometry))
    monkeypatch.setattr(native_identity, "application_identity", lambda *a: actual_identity())
    monkeypatch.setattr(drc, "registered_engine", lambda: "MGCPCBEngines.DRC.151")
    monkeypatch.setattr(bridge, "_layout_board_path", lambda p: board)
    monkeypatch.setattr(bridge, "_application", lambda *a, **k: app)
    monkeypatch.setattr(bridge, "_licensed_document", lambda a: a.ActiveDocument)
    monkeypatch.setattr(bridge, "_settle", lambda *a: None)
    monkeypatch.setattr(bridge, "_native_call", lambda name, fn: fn())
    monkeypatch.setattr(
        "xpedition_cli.driver._owned_process_identity",
        lambda *a: process_identity(
            board.parent.parent,
            birth="200000000" if fault == "pid_reuse" and "Close" in events else "100000000",
        ),
    )
    read = drc._read_contents

    def contents(path, **kwargs):
        if fault == "log_access" and "Close" in events and Path(path).name == "DrcCompTypeLog.txt":
            raise PermissionError("synthetic lifecycle-log access denied")
        return read(path, **kwargs)

    monkeypatch.setattr(drc, "_read_contents", contents)

    class Backend:
        def invoke(self, method, params, **kwargs):
            methods.append({"method": method, "closed": app.ActiveDocument is None})
            if method == "identity":
                return {
                    "application": actual_identity(),
                    "adapter": _worker_fixture(
                        "b" * 64 if fault == "worker_drift" and "Close" in events else "a" * 64
                    ),
                }
            if method in {"snapshot", "cell_refresh_state"}:
                assert app.ActiveDocument is not None, "No generic snapshot may run after Close"
                return (
                    {"components": [], "fixture": True}
                    if method == "snapshot"
                    else {"geometry": copy.deepcopy(geometry)}
                )
            assert method == "batch_drc" and params["run"] is True, (
                "No Open or no-run reopen fallback"
            )
            try:
                return bridge._batch_drc(params, SimpleNamespace(Dispatch=lambda name: Engine()))
            except bridge.AdapterError as error:
                if fault == "report_replaced":
                    paths["report"].write_text(
                        "synthetic later replacement; stable but not consumed\n"
                    )
                raise CLIError(error.code, error.message, error.details) from error

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "checks",
        "run",
        "--project",
        str(project),
        "--rules",
        inputs["rules"],
        "--elements",
        inputs["elements"],
    ]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        assert not events
        token = preview.envelope["data"]["confirm_token"]
        failed = cli.run([*args, "--confirm", token, "--quiet"])
        assert not failed.envelope["ok"], failed.envelope
        assert "receipt" in failed.envelope["error"]["details"], failed.envelope["error"]
        receipt = failed.envelope["error"]["details"]["receipt"]
        result = receipt["extensions"]["result"]
        assert receipt["extensions"]["save_acknowledged"] is (fault != "save_reply_lost")
        assert receipt["outcome"]["verification"]["status"] == "incomplete"
        assert receipt["outcome"]["persistence"] != "saved"
        assert receipt["extensions"]["persistence_independently_reopened"] is False
        assert result.get("persistence_verified") is not True
        observation = receipt["extensions"]["file_readback"]
        assert observation["complete"] is (fault is None)
        assert len(observation["second"]["files"]) == 12
        assert len(observation["binding"]["side_effects"]) == 10
        assert observation["reopen"]["authorized"] is observation["reopen"]["attempted"] is False
        if fault == "log_access":
            assert (
                observation["second"]["files"]["component_type_log"]["read_error"]["type"]
                == "PermissionError"
            )
            assert observation["second"]["files"]["board_log"]["exists"] is True
        if fault == "report_replaced":
            assert {"reason": "consumed_output_changed", "role": "report"} in observation["issues"]
        if fault != "save_reply_lost":
            assert result["board_lifecycle"]["save_returned"] is True
            assert result["board_lifecycle"]["save_reply_received"] is True
            assert result["unqualified_report_observation"]["execution_qualified"] is False
            assert events == ["Save", "Close", "Go"]
        else:
            assert events == ["Save"]
        operation_id = failed.envelope["error"]["details"]["operation_id"]
        stored = copy.deepcopy(cli._store.get(operation_id))
        assert stored["status"] == "unknown" and stored["resolution"] is None
        lookup = cli.run(["operation", "show", "--id", operation_id, "--quiet"])
        assert lookup.envelope["data"]["operation_status"]["receipt"] == stored["receipt"]
        replay = cli.run([*args, "--confirm", token, "--quiet"])
        assert not replay.envelope["ok"]
        assert cli._store.get(operation_id)["receipt"] == stored["receipt"]
        assert sum(m["method"] == "batch_drc" for m in methods) == 1
        assert not any(
            m["closed"] and m["method"] != "identity" and m["method"] != "batch_drc"
            for m in methods
        )
    if output := os.environ.get("XPEDITION_DRC_REVIEW_EVIDENCE"):
        exported = {
            "kind": "offline_state_transition_not_native_acceptance",
            "fault": fault,
            "error_code": failed.envelope["error"]["code"],
            "outcome": receipt["outcome"],
            "save_acknowledged": receipt["extensions"]["save_acknowledged"],
            "lifecycle": result["board_lifecycle"],
            "events": events,
            "methods": methods,
            "observation": observation,
            "status": stored["status"],
            "lookup_and_replay_preserved_receipt": True,
        }
        text = json.dumps(exported, indent=2, ensure_ascii=False).replace(
            str(tmp_path).replace("\\", "\\\\"), "<FIXTURE_ROOT>"
        )
        path = Path(output) / ("closed-failure-" + str(fault) + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
