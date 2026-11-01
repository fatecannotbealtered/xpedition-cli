"""Synthetic native reports through the public check command; no CAD corruption."""

import copy
import json
import time

import pytest
from drc_fakes import application_identity, process_identity
from test_driver_v2 import _scope, _worker_fixture
from test_refresh_review import prepare_project

from xpedition_cli import drc_engine as D
from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


def report_files(board):
    paths = D.outputs(board)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["effective_rules"].write_text(
        '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.UNROUTEDPINS YES\n.SINGLEPOINTNETS NO\n'
    )
    paths["effective_elements"].write_text('.FILETYPE BATCH_DRC_E2E_FILE\n.VERSION "2.02"\n')
    paths["report"].write_text(
        "DRC\n===\n检查未布线的管脚 : YES\nTotal DRC Hazards Found : 1\n", encoding="utf-8"
    )
    paths["violations"].write_text(
        '.FILETYPE DRCFILE\n.VERSION "2002"\n.DATE "Today"\n.TIME "Now"\n'
        ".UNROUTED_NET\n..XY (1000000, 2000000)\n..OBJECT\n...PAD PART\n"
        '....REF_DES "R1"\n....NET "QA_SIGNAL"\n....XY (1000000, 2000000)\n'
    )
    return paths


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "old_report",
        "missing_report",
        "corrupt_report",
        "corrupt_violations",
        "timeout",
        "geometry_read_error",
        "geometry_mismatch",
        "wrong_hazard_type",
        "wrong_hazard_object",
        "wrong_hazard_position",
        "missing_config_binding",
        "cover_rule_not_executed",
        "via_cover_rule_not_executed",
    ],
)
def test_public_native_check_preview_is_read_only_and_results_are_fresh(
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
    board.write_bytes(b"synthetic board")
    paths = report_files(board)
    selected = {}
    for kind in ("rules", "elements"):
        source = tmp_path / ("selected-" + kind)
        content = paths["effective_" + kind].read_text()
        if kind == "rules":
            content += ".MINIMUMANNULARRING NO\n.MINIMUMANNULARRINGVALUE 254000\n"
            if fault in {"cover_rule_not_executed", "via_cover_rule_not_executed"}:
                content += (
                    ".PARTCOVERLAYER YES\n"
                    if fault == "cover_rule_not_executed"
                    else ".VIACOVERLAYER YES\n"
                )
        source.write_text(content)
        selected[kind] = D.file_record(source)
    calls = []
    state = {"components": [], "model": {"pads": []}, "stackup": []}
    monkeypatch.setattr(
        "xpedition_cli.driver._owned_process_identity",
        lambda *a: process_identity(board.parent.parent),
    )

    class Backend:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {"application": application_identity(board), "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                assert kwargs["timeout_seconds"] == 600.0
                return {"geometry": copy.deepcopy(state)}
            if method == "board_geometry":
                if fault == "geometry_read_error":
                    raise CLIError(
                        "E_NOT_FOUND", "synthetic later read failure", {"write_attempted": False}
                    )
                return {
                    "model": {
                        "pads": [{"net": "unexpected"}] if fault == "geometry_mismatch" else [],
                        "components": [],
                        "physical_instances": [],
                        "stackup": [],
                    }
                }
            assert method == "batch_drc"
            calls.append(bool(params["run"]))
            if not params["run"]:
                hazard = {
                    "kind": "PartialNets",
                    "x": 1.0,
                    "y": 2.0,
                    "object_details": [
                        {"refdes": "R1", "net": "QA_SIGNAL", "position_mm": [1.0, 2.0]}
                    ],
                }
                if fault == "wrong_hazard_type":
                    hazard["kind"] = "OtherType"
                elif fault == "wrong_hazard_object":
                    hazard["object_details"][0]["refdes"] = "J99"
                elif fault == "wrong_hazard_position":
                    hazard["x"] = 9.0
                return {"hazards": [hazard], "online": []}
            if fault == "timeout":
                raise CLIError("E_TIMEOUT", "synthetic checker timeout")
            before = {k: D.file_record(v) for k, v in paths.items()}
            started = time.time_ns()
            if fault != "old_report":
                report_files(board)
                with paths["effective_rules"].open("a") as stream:
                    stream.write(".MINIMUMANNULARRING NO\n.MINIMUMANNULARRINGVALUE 10\n")
                    if fault in {"cover_rule_not_executed", "via_cover_rule_not_executed"}:
                        stream.write(
                            ".PARTCOVERLAYER YES\n"
                            if fault == "cover_rule_not_executed"
                            else ".VIACOVERLAYER YES\n"
                        )
                if fault in {"cover_rule_not_executed", "via_cover_rule_not_executed"}:
                    label = "元件" if fault == "cover_rule_not_executed" else "通孔"
                    paths["report"].write_text(
                        f"DRC\n===\n检查未布线的管脚 : YES\n检查缺少的{label}覆盖层焊盘 : NO\n"
                        "Total DRC Hazards Found : 1\n",
                        encoding="utf-8",
                    )
            if fault == "missing_report":
                paths["report"].unlink()
            elif fault == "corrupt_report":
                paths["report"].write_bytes(b"DRC incomplete")
            elif fault == "corrupt_violations":
                paths["violations"].write_bytes(b"invalid")
            try:
                fresh = D.read_reports(
                    paths,
                    before,
                    started,
                    selected=None if fault == "missing_config_binding" else selected,
                    engine_version="MGCPCBEngines.DRC.151",
                )
            except ValueError as error:
                raise CLIError("E_NATIVE_FAILURE", str(error)) from error
            result = {
                "pcb": str(board),
                # This fake has finished Go/report parsing, just like the real
                # returned worker packet. Timeout paths above never reach it.
                "native_go_dispatched": True,
                "native_go_returned": True,
                "native_stages": {"go": "returned", "report_read": "complete"},
                "ran": True,
                "completed": True,
                "count": 1,
                "passes": False,
                "clean": False,
                "geometry_before": state,
                "persistence_verified": False,
                "change_status": "applied",
                "persistence_state": "partial",
                "report_before": before,
                "report_started_ns": started,
                "staged_input_configurations": None
                if fault == "missing_config_binding"
                else selected,
                "native_reports": fresh,
                "completion": {"status": "completed", "timed_out": False},
                "source": {
                    "kind": "native_checker",
                    "complete": True,
                    "freshness": "current_run",
                    "application_version": "151.0 fixture",
                    "checker_version": "MGCPCBEngines.DRC.151",
                    "input": {
                        "requested_project": str(project),
                        "observed_pcb": str(board),
                        "snapshot_sha256": "a" * 64,
                    },
                    "coverage": fresh,
                },
            }
            # This crosses the real worker's JSON boundary. Direct Python return
            # values hid tuple/list differences in inactive-rule diagnostics.
            return json.loads(json.dumps(result))

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = ["pcb", "checks", "run", "--project", str(project)]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        assert not calls
        assert (
            "persist"
            in preview.envelope["data"]["preview"]["extensions"]["legacy_preview"]["effects"]
        )
        lifecycle = preview.envelope["data"]["preview"]["extensions"]["legacy_preview"][
            "native_check_plan"
        ]["lifecycle"]
        plan = preview.envelope["data"]["preview"]
        side_effects = plan["extensions"]["legacy_preview"]["native_check_plan"][
            "declared_side_effects"
        ]
        assert {row["role"] for row in side_effects} == {
            "report",
            "violations",
            "effective_rules",
            "effective_elements",
            "staged_inputs",
            "component_type_log",
            "automation_log",
            "autosave_log",
            "board_info_log",
            "board_log",
        }
        assert {(row["path"], row["effect"]) for row in side_effects} <= {
            (row["path"], row["effect"]) for row in plan["outputs"]
        }
        assert not any("WriteSetupFile" in step for step in lifecycle)
        preserve = "copy and hash prior effective configurations before Go; keep original paths"
        fresh = "consume only fresh automatic Config outputs produced after this Go"
        go = lifecycle.index("native engine Go once")
        assert lifecycle.index(preserve) < go < lifecycle.index(fresh)
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    if fault is None:
        assert result.envelope["ok"], json.dumps(result.envelope)
        receipt = result.envelope["data"]["receipt"]
        assert receipt["outcome"]["verification"]["status"] == "passed"
        assert receipt["extensions"]["result"]["clean"] is False
        assert receipt["extensions"]["result"]["persistence_state"] == "saved"
        assert receipt["extensions"]["result"]["persistence_verified"] is True
        with runtime(Adapter(backend), state_dir=tmp_path / "state") as reader:
            stored = reader.run(
                ["pcb", "checks", "result", "--id", receipt["extensions"]["result"]["check_run_id"]]
            )
        assert stored.envelope["data"]["report"]["persistence_state"] == "saved"
        assert calls == [True, False]
    else:
        assert not result.envelope["ok"]
        if fault in {
            "missing_config_binding",
            "cover_rule_not_executed",
            "via_cover_rule_not_executed",
        }:
            assert calls == [True, False]
            receipt = result.envelope["error"]["details"]["receipt"]
            assert receipt["outcome"]["verification"]["status"] == "incomplete"
            if fault in {"cover_rule_not_executed", "via_cover_rule_not_executed"}:
                assert receipt["outcome"]["persistence"] == "saved"
                observed = receipt["extensions"]["result"]
                assert not observed["passes"] and not observed["clean"]
                assert (
                    "selected_rule_execution_confirmed"
                    in observed["verification"]["missing_evidence"]
                )
            return
        if fault in {
            "geometry_read_error",
            "geometry_mismatch",
            "wrong_hazard_type",
            "wrong_hazard_object",
            "wrong_hazard_position",
        }:
            assert calls == [True, False]
            error = result.envelope["error"]
            assert error["details"]["dispatch_state"] == "dispatched"
            assert error["details"]["receipt"]["outcome"]["persistence"] == "partial"
        else:
            assert calls == [True]  # No stale hazard consumption after failure/timeout.
