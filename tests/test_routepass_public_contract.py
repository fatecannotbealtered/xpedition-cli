"""Public discovery/admission only; synthetic processes do not prove native routing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_main():
    from xpedition_cli import driver
    from xpedition_cli.cli import pcb
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_ROUTEPASS_PROCESS_FIXTURE"])
    calls = []
    patches = pytest.MonkeyPatch()

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            calls.append({"method": method, "apply": params.get("apply"), "params": params})
            if synthetic_mode == "component-observation":
                assert method == "board_info" and params["start"] is False
                return {
                    "pcb": params["project"],
                    "observation": {
                        "engineering_write_dispatched": False,
                        "persistence": "unknown",
                        "native_routepass_request": params["routepass_request"],
                        "native_routepass_components": {
                            "valid": True,
                            "observation_valid": True,
                            "complete": False,
                            "completion_valid": False,
                            "all_members_connected": False,
                            "components": [
                                {"pin_ids": ["a", "b", "c"]},
                                {"pin_ids": ["d", "e"]},
                                {"pin_ids": ["f"]},
                            ],
                        },
                    },
                }
            raise AssertionError("Invalid/legacy route requests must stop before native reads")

    synthetic_mode = os.environ.get("XPEDITION_ROUTEPASS_PROCESS_MODE", "reject-native")
    backend = Backend()
    patches.setattr(driver, "NativeBackend", lambda: backend)
    patches.setattr(pcb, "native", lambda: backend)
    patches.setattr(pcb, "reader", lambda _: backend)
    try:
        return main()
    finally:
        (folder / "routepass-process-native-events.json").write_text(
            json.dumps(calls), encoding="utf-8"
        )
        patches.undo()


def run_process(folder, *args, synthetic=False, synthetic_mode="reject-native"):
    scope = folder / "routepass-process-scope.json"
    scope.write_text(
        json.dumps(
            {
                "scope_id": "owned-offline-routepass-process",
                "tool": "xpedition-cli",
                "granted_by": "owned synthetic process fixture",
                "granted_at": "2026-10-18T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "session", "artifact", "change", "persist", "configure"],
                "dangerous": True,
                "roots": [str(folder)],
                "outputs": [str(folder)],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(folder / "config"),
        "XPEDITION_NATIVE_COMMAND": str(folder / "no-live-adapter.exe"),
        "XPEDITION_ROUTEPASS_PROCESS_FIXTURE": str(folder),
        "XPEDITION_ROUTEPASS_PROCESS_MODE": synthetic_mode,
        "XPEDITION_CLI_SCOPE_FILE": str(scope),
    }
    if synthetic:
        bootstrap = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'tests')!r}); "
            "from test_routepass_public_contract import _synthetic_main; "
            "raise SystemExit(_synthetic_main())"
        )
        prefix = [sys.executable, "-c", bootstrap]
    else:
        exe = os.environ.get("XPEDITION_CLI_PROCESS_EXE")
        prefix = [exe] if exe else [sys.executable, "-m", "xpedition_cli"]
    result = subprocess.run(
        [*prefix, *args, "--compact"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    body = json.loads(result.stdout)
    assert (result.returncode == 0) is body["ok"], (body, result.stderr)
    return result.returncode, body


def test_real_public_discovery_exposes_mode_boundary_and_keeps_65_leaves(tmp_path):
    code, body = run_process(tmp_path, "reference", "--command", "pcb routing auto")
    assert code == 0
    reference = body["data"]
    command = reference["command"]
    params = {row["name"]: row for row in command["params"]}
    assert params["mode"]["enum"] == ["auto", "bounded-native", "bounded-general"]
    assert "default legacy auto mode remains unsupported" in command["description"]
    assert "effective-rule parsing or DRC" in command["description"]
    assert "exactly one" in params["nets"]["description"]
    assert "exactly one" in params["layers"]["description"]
    assert "closed source recovery ZIP" in params["source-backup"]["description"]
    assert all("forbidden" in params[name]["description"] for name in ("passes", "unroute"))
    schemas = reference["schemas"]
    result = schemas["pcb_route"]
    assert result["native_routepass_supported_member_counts"] == [2, 3, 6]
    six = result["native_routepass_six_pin_scope"]
    assert six["net"] == "GND"
    assert sorted(six["physical_members"]) == ["C1.2", "C2.2", "J1.2", "J2.2", "J3.2", "J4.2"]
    assert six["initial_opens"] == 5 and six["old_target_copper"] is False
    assert "continue-existing" in params
    continuation = result["native_routepass_continuation_scope"]
    assert continuation["request_value"] == "existing-components"
    assert continuation["old_target_trace_count"] == 3
    assert continuation["old_board_trace_count"] == 11
    assert continuation["material_components_before"] == 3
    assert continuation["initial_opens"] == 2
    assert continuation["default_old_target_copper_refused"] is True
    assert continuation["old_trace_identity_and_full_raw_material_preserved"] is True
    assert (
        "continuous post-drill/post-cutout pad regions"
        in result["native_routepass_material_graph_boundary"]
    )
    assert "trace-only material component" not in result["native_routepass_material_graph_boundary"]
    assert result["required"] == ["pcb"]
    assert {"unrouted_before", "unrouted", "runs", "prompts"}.isdisjoint(result["required"])
    assert {
        "mode",
        "routepass",
        "stages",
        "native_calls_returned",
        "verification",
        "saved",
        "persistence_verified",
        "state_after",
        "state_reopened",
        "reopen_evidence",
        "save_gate_checkpoint_evidence",
        "observable_state_guard_evidence",
        "selected_net_evidence",
        "state_before_go",
        "pre_go_input_observation",
        "state_before_save",
        "post_route_input_observation",
        "dependency_change_evidence",
        "pre_save_input_observation",
    } <= set(result["native_routepass_required_fields"])
    assert "Successful bounded-native apply" in result["native_routepass_required_field_scope"]
    assert {"routepass", "verification", "state_after", "state_reopened"} <= set(
        result["untrusted_fields"]
    )
    assert {"routepass", "state_after", "state_reopened", "stages", "reopen_evidence"} <= set(
        result["fields"]
    )
    assert "created_objects" in result["native_routepass_verification_shape"]
    assert "physical_connection" in result["native_routepass_verification_shape"]
    assert "configuration_readback" in result["native_routepass_configuration_shape"]
    assert (
        result["native_routepass_guard"]
        == "native-routepass-two-three-or-six-pad-no-via-material-graph-v8"
    )
    assert {"role", "may_create", "existing_report_indices?", "max_bytes?"} <= set(
        result["native_routepass_allowed_output_shape"]
    )
    assert result["native_routepass_text_observation_shape"] == ["encoding", "text", "_untrusted"]
    output_boundary = result["native_routepass_allowed_output_boundary"]
    for text in ("next absent AutoRouteReportNN.txt", "MVO_rules_log.txt", "1MiB", "never a whole"):
        assert text in output_boundary
    assert {"native_mask_proof", "native_trace_material"} <= set(
        result["native_routepass_material_state_fields"]
    )
    assert {"width_mm", "points_array_mm", "bounds_mm", "bounds_source"} <= set(
        result["native_routepass_raw_trace_material_shape"]
    )
    assert {
        "cleanup",
        "temporary_masks_count",
        "target_contacts",
        "target_pad_regions",
        "target_pad_intersections",
    } <= set(result["native_routepass_mask_proof_shape"])
    assert "local to this observation" in result["native_routepass_pad_continuity_boundary"]
    assert result["native_routepass_checkpoint_observation_phases"] == [
        "before_configuration",
        "immediately_before_go",
        "after_go_before_readback",
        "immediately_before_save",
    ]
    assert {"status", "atomic_freeze_proven", "error?"} <= set(
        result["native_routepass_checkpoint_shape"]
    )
    assert {"atomic_database_freeze", "exclusive_database_lock", "observations"} <= set(
        result["native_routepass_observable_state_guard_shape"]
    )
    assert {"design_state_sha256", "dependency_identity_sha256", "material_basis"} <= set(
        result["native_routepass_observable_observation_shape"]
    )
    assert result["native_routepass_selected_net_shape"] == ["count", "net", "net_uid"]
    assert result["native_routepass_observable_phases"] == [
        "initial_bind",
        "immediately_before_go",
        "post_go_readback",
        "immediately_before_save",
    ]
    boundary = result["native_routepass_boundary"]
    for text in (
        "NetsItem5",
        "Go is formal void",
        "BOOL=True",
        "No supported pass configuration getter",
        "SUB0",
        "AND1",
        "not persistent design identity",
        "library and rule-file changes stop Save",
        "Trace.Extrema",
        "Checkpoint is observation only",
        "observed Before1 is not formal At2",
        "doc.Nets enumeration and net UID",
        "opaque database/cache equality",
    ):
        assert text in boundary
    assert "saves/closes before execution" in boundary
    from xpedition_cli.reference_data import reference as handler_reference

    # The public SDK's dry-run schema is driver_plan. The handler schema also
    # documents the task-specific payload nested in legacy_preview.
    assert command["dry_run_output_schema"] == "driver_plan"
    preview = handler_reference(command="pcb routing auto")["schemas"]["pcb_route_preview"]
    assert preview["preview_is_route_execution"] is False
    assert preview["native_routepass_boundary"] == boundary
    code, body = run_process(tmp_path, "reference", "--index")
    assert code == 0 and len(body["data"]["commands"]) == 65
    code, body = run_process(tmp_path, "reference", "--command", "pcb routing trace")
    assert code == 0 and "can_apply=false" in body["data"]["command"]["description"]


def test_real_public_live_inspect_discovery_exposes_material_read_without_route(tmp_path):
    from xpedition_cli.backends.native_xpedition import NativeBackend
    from xpedition_cli.operation_budget import profile_for

    code, body = run_process(tmp_path, "reference", "--command", "pcb inspect")
    assert code == 0
    reference = body["data"]
    params = {row["name"]: row for row in reference["command"]["params"]}
    assert "exactly one" in params["nets"]["description"]
    assert "exactly one" in params["layers"]["description"]
    profile = profile_for("pcb inspect")
    live_description = params["live-geometry"]["description"]
    for seconds in (
        profile["native_call_seconds"]["board_info"],
        profile["stage_seconds"]["query"],
        profile["total_seconds"]["query"],
    ):
        assert f"{seconds} seconds" in live_description
    assert "maximum180seconds" not in live_description
    default_read = NativeBackend.read_timeout_seconds
    assert f"default to {int(default_read)} seconds" in params["timeout"]["description"]
    assert "leaves the session stale" not in params["timeout"]["description"]
    schema = reference["schemas"]["pcb_info"]
    assert {
        "native_routepass_state",
        "native_routepass_request",
        "native_routepass_state_sha256",
        "native_routepass_evidence",
        "native_routepass_identity",
        "native_routepass_consistency",
    } <= set(schema["observation_shape"])
    assert schema["observation_native_routepass_request_shape"] == ["net", "layer", "continuation?"]
    assert "continue-existing" in params
    assert "native_routepass_components" in schema["observation_shape"]
    assert {"observation_valid", "completion_valid", "components"} <= set(
        schema["observation_native_routepass_components_shape"]
    )
    assert "false completion" in schema["observation_native_routepass_components_boundary"]
    assert schema["observation_native_routepass_identity_shape"] == [
        "application",
        "generation",
        "board",
    ]
    boundary = schema["observation_native_routepass_boundary"]
    for text in (
        "observe-only",
        "no RoutePass configuration/Go",
        "historical",
        "partial",
        "published",
    ):
        assert text in boundary


def test_real_public_labels_reference_discloses_selected_envelope_precision(tmp_path):
    code, body = run_process(tmp_path, "reference", "--command", "pcb silkscreen labels")
    assert code == 0
    schema = body["data"]["schemas"]["pcb_labels"]
    assert "explicit_silk_translation_v2" in schema["note"]
    precision = schema["selected_envelope_comparison"]
    assert precision["scope"] == "selected text inventory extents only"
    assert precision["unit"] == "mm" and precision["decimal_places"] == 4
    assert precision["epsilon"] is None
    assert precision["raw_before_after_witnesses_retained"] is True
    assert precision["actual_witness_self_consistency_required"] is True
    assert precision["non_target_fields_quantized"] is False
    assert precision["full_geometry_comparison_changed"] is False


def test_bounded_mode_example_parses_without_new_command_leaf():
    from xpedition_cli.main import parse_argv

    command, options = parse_argv(
        "pcb routing auto --project owned.prj --mode bounded-native --nets A_IN --layers 1 "
        "--source-backup source.zip "
        "--dry-run".split()
    )
    assert command.path == "pcb routing auto"
    assert options["mode"] == "bounded-native"
    assert options["nets"] == "A_IN" and options["layers"] == "1"


@pytest.mark.parametrize(
    "selection",
    [
        [],
        ["--nets", "A_IN"],
        ["--layers", "1"],
        ["--nets", "A_IN,B_IN", "--layers", "1"],
        ["--nets", "A_IN", "--layers", "1,2"],
        ["--nets", "A_IN", "--layers", "0"],
        ["--nets", "A_IN", "--layers", "one"],
        ["--nets", "A_IN", "--layers", "1", "--passes", "route:1-5"],
        ["--nets", "A_IN", "--layers", "1", "--unroute"],
        ["--nets", "A_IN", "--layers", "1", "--native-effort", "0", "--source-backup", "owned.zip"],
        ["--nets", "A_IN", "--layers", "1", "--native-effort", "3", "--source-backup", "owned.zip"],
        [
            "--nets",
            "A_IN",
            "--layers",
            "1",
            "--native-effort",
            "true",
            "--source-backup",
            "owned.zip",
        ],
        ["--nets", "A_IN", "--layers", "1"],
        ["--nets", "A_IN", "--layers", "1", "--source-backup", "owned.zip", "--continue-existing"],
    ],
)
def test_public_invalid_bounded_requests_have_zero_native_calls(tmp_path, selection):
    project = tmp_path / "owned.pcb"
    project.write_bytes(b"owned offline fixture")
    code, body = run_process(
        tmp_path,
        "pcb",
        "routing",
        "auto",
        "--project",
        str(project),
        "--mode",
        "bounded-native",
        *selection,
        "--dangerous",
        "--dry-run",
        synthetic=True,
    )
    assert code == 2 and body["error"]["code"] in {"E_USAGE", "E_VALIDATION"}, body
    assert body["error"]["details"]["dispatch_state"] == "not_started"
    assert json.loads((tmp_path / "routepass-process-native-events.json").read_text()) == []


def test_real_public_process_preserves_complete_but_incomplete_component_observation(tmp_path):
    project = tmp_path / "owned.pcb"
    project.write_bytes(b"owned offline fixture")
    code, body = run_process(
        tmp_path,
        "pcb",
        "inspect",
        "--project",
        str(project),
        "--live-geometry",
        "--pid",
        "42",
        "--nets",
        "GND",
        "--layers",
        "1",
        "--continue-existing",
        synthetic=True,
        synthetic_mode="component-observation",
    )
    assert code == 0, body
    observed = body["data"]["observation"]
    assert observed["native_routepass_request"] == {
        "net": "GND",
        "layer": 1,
        "continuation": "existing-components",
    }
    components = observed["native_routepass_components"]
    assert components["observation_valid"] is True
    assert components["completion_valid"] is False and len(components["components"]) == 3
    calls = json.loads((tmp_path / "routepass-process-native-events.json").read_text())
    assert len(calls) == 1 and calls[0]["method"] == "board_info"
    assert observed["engineering_write_dispatched"] is False
    assert observed["persistence"] == "unknown"


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--nets", "GND", "--layers", "1"],
        ["--live-geometry", "--pid", "42", "--nets", "A_IN", "--layers", "1"],
        ["--live-geometry", "--pid", "42", "--nets", "GND"],
        ["--live-geometry", "--pid", "42", "--nets", "GND", "--layers", "1,2"],
    ],
)
def test_public_continuation_observation_requires_explicit_exact_read_scope(tmp_path, extra):
    project = tmp_path / "owned.pcb"
    project.write_bytes(b"owned offline fixture")
    code, body = run_process(
        tmp_path,
        "pcb",
        "inspect",
        "--project",
        str(project),
        "--continue-existing",
        *extra,
        synthetic=True,
    )
    assert code == 2 and body["error"]["code"] == "E_VALIDATION", body
    assert json.loads((tmp_path / "routepass-process-native-events.json").read_text()) == []


@pytest.mark.parametrize("explicit", [False, True])
def test_public_legacy_auto_stays_refused_before_native_reads(tmp_path, explicit):
    project = tmp_path / "owned.pcb"
    project.write_bytes(b"owned offline fixture")
    code, body = run_process(
        tmp_path,
        "pcb",
        "routing",
        "auto",
        "--project",
        str(project),
        *(["--mode", "auto"] if explicit else []),
        "--dangerous",
        "--dry-run",
        synthetic=True,
    )
    assert code != 0 and body["error"]["code"] == "E_UNSUPPORTED", body
    assert body["error"]["details"]["dispatch_state"] == "not_started"
    assert json.loads((tmp_path / "routepass-process-native-events.json").read_text()) == []
