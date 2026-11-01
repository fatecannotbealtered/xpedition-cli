"""Fresh public CLI processes with synthetic native objects, never CAD acceptance.

The subprocess bootstrap replaces only native attachment/capture and backend
transport. Public parser/SDK, native copper execute/observation, verification and
durable operation queries run normally. No new production test hook is added.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _synthetic_main():
    """Run exactly one actual public invocation against an owned synthetic board."""
    import pytest
    from test_copper_native import native_case
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli import board_copper as copper
    from xpedition_cli import cell_refresh, driver, native_identity
    from xpedition_cli import native_com_adapter as native
    from xpedition_cli.cli import pcb
    from xpedition_cli.errors import CLIError
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_COPPER_PROCESS_FIXTURE"])
    patches = pytest.MonkeyPatch()
    _scope(folder, patches)
    app, params, _, _ = native_case(
        folder,
        patches,
        os.environ.get("XPEDITION_COPPER_PROCESS_FAULT", "reopen_rectangle_winding"),
    )
    identity = {"pid": 4242, "fixture": True}
    patches.setattr(native_identity, "application_identity", lambda *args: identity)
    patches.setattr(cell_refresh, "capture", copper.capture)
    if sys.argv[1:3] == ["pcb", "inspect"]:
        # Prepare a pre-existing synthetic copper state. No real board is touched.
        preview = copper.execute(params, object(), native)
        copper.execute(
            {**params, "apply": True, "expected": preview["copper"]["fingerprint"]},
            object(),
            native,
        )
        from test_copper_ties import install_inventory

        install_inventory(app.ActiveDocument, os.environ.get("XPEDITION_COPPER_PROCESS_FAULT"))

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, options, **kwargs):
            if method == "identity":
                return {"application": identity, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            try:
                if method == "cell_refresh_state":
                    assert options["strict"] and options["plane_objects"]
                    return copper.observe(app.ActiveDocument, native)
                if method == "plane_pour":
                    return copper.execute(options, object(), native)
                if method == "board_info":
                    assert options["geometry"] and options["start"] is False
                    return native._board_observation(options, object())
                raise AssertionError(method)
            except native.AdapterError as error:
                raise CLIError(error.code, str(error), error.details) from error

    backend = Backend()
    patches.setattr(driver, "NativeBackend", lambda: backend)
    patches.setattr(pcb, "native", lambda: backend)
    patches.setattr(pcb, "reader", lambda options: backend)
    try:
        return main()
    finally:
        patches.undo()


def run_process(tmp_path, *args, synthetic=False, fault=None, expect_ok=True):
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(tmp_path / "config"),
        "XPEDITION_COPPER_PROCESS_FIXTURE": str(tmp_path),
        "XPEDITION_NATIVE_COMMAND": str(tmp_path / "no-live-adapter.exe"),
        "XPEDITION_COPPER_PROCESS_FAULT": fault or "reopen_rectangle_winding",
    }
    if synthetic:
        # Actual public main emits the sole stdout JSON; no handler harness.
        bootstrap = (
            "import sys; "
            f"sys.path.insert(0, {str(TESTS)!r}); "
            "from test_copper_evidence_process import _synthetic_main; "
            "raise SystemExit(_synthetic_main())"
        )
        command = [sys.executable, "-c", bootstrap, *args, "--compact"]
    else:
        executable = os.environ.get("XPEDITION_CLI_PROCESS_EXE")
        prefix = [executable] if executable else [sys.executable, "-m", "xpedition_cli"]
        command = [*prefix, *args, "--compact"]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    body = json.loads(result.stdout)
    assert (result.returncode == 0) is expect_ok, (body, result.stderr)
    assert body["ok"] is expect_ok, body
    return body["data"] if expect_ok else body["error"]


def assert_boundary(boundary):
    assert set(boundary) == {
        "draft_rectangle",
        "generated_body_and_cutouts",
        "full_thermal_tie_conductors",
        "native_connectivity",
        "drc",
        "persistence",
    }
    assert boundary["full_thermal_tie_conductors"]["independently_proven"] is False
    assert "pad contact" in " ".join(boundary["full_thermal_tie_conductors"]["missing_evidence"])


def test_fresh_public_discovery_exposes_physical_boundary(tmp_path):
    reference = run_process(tmp_path, "reference", "--command", "pcb copper pour")
    assert "thermal/tie" in reference["command"]["description"]
    schema = reference["schemas"]["pcb_pour"]
    assert "evidence_boundary" in schema["copper_plan_shape"]
    assert_boundary(schema["copper_evidence_boundary"])
    inspect = run_process(tmp_path, "reference", "--command", "pcb inspect")
    assert_boundary(inspect["schemas"]["pcb_info"]["copper_evidence_boundary"])


def test_fresh_public_preview_receipt_and_retained_query_preserve_scope(tmp_path):
    args = [
        "pcb",
        "copper",
        "pour",
        "--project",
        str(tmp_path / "owned.pcb"),
        "--net",
        "A",
        "--layer",
        "2",
        "--margin",
        "1",
    ]
    preview = run_process(tmp_path, *args, "--dry-run", synthetic=True)
    plan = preview["preview"]["extensions"]["legacy_preview"]["copper"]
    assert_boundary(plan["evidence_boundary"])
    assert "thermal/tie" in " ".join(plan["not_checked"])
    confirmed = run_process(tmp_path, *args, "--confirm", preview["confirm_token"], synthetic=True)
    receipt = confirmed["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "saved"
    proof = receipt["extensions"]["file_readback"]
    assert proof["valid"] is True and proof["engineering_complete"] is False
    assert_boundary(proof["evidence_boundary"])
    comparison = receipt["extensions"]["result"]["reopen_comparison"]
    assert comparison["equal"] is False
    assert comparison["geometry_equal"] is True
    assert comparison["plane_state_equal"] is False
    assert comparison["persistence_equivalent"] is True
    assert comparison["equivalence_basis"] == "target_rectangle_vertex_order"
    query = run_process(tmp_path, "operation", "show", "--id", preview["preview"]["operation_id"])
    assert query["operation_status"]["receipt"] == receipt


def test_fresh_public_live_read_exposes_limits_without_saving_claim(tmp_path):
    result = run_process(
        tmp_path,
        "pcb",
        "inspect",
        "--project",
        str(tmp_path / "owned.pcb"),
        "--live-geometry",
        "--pid",
        "4242",
        synthetic=True,
    )
    observation = result["observation"]
    assert observation["plane_state"]["generated"]
    assert_boundary(observation["copper_evidence_boundary"])
    assert "thermal/tie" in " ".join(observation["limitations"])
    assert observation["persistence"] == "unknown"
    assert observation["engineering_write_dispatched"] is False


def test_fresh_public_failed_protection_keeps_boundary_and_stops_lifecycle(tmp_path):
    args = [
        "pcb",
        "copper",
        "pour",
        "--project",
        str(tmp_path / "owned.pcb"),
        "--net",
        "A",
        "--layer",
        "2",
        "--margin",
        "1",
    ]
    preview = run_process(tmp_path, *args, "--dry-run", synthetic=True, fault="generated_hole")
    error = run_process(
        tmp_path,
        *args,
        "--confirm",
        preview["confirm_token"],
        synthetic=True,
        fault="generated_hole",
        expect_ok=False,
    )
    receipt = error["details"]["receipt"]
    assert receipt["outcome"]["persistence"] == "unknown"
    proof = receipt["extensions"]["file_readback"]
    assert proof["valid"] is False
    assert_boundary(proof["evidence_boundary"])
    result = receipt["extensions"]["result"]
    assert result["saved"] is False
    assert all(result["stages"][step] == "not_started" for step in ("save", "close", "reopen"))
    retained = run_process(
        tmp_path, "operation", "show", "--id", preview["preview"]["operation_id"]
    )
    assert retained["operation_status"]["receipt"] == receipt


def test_fresh_public_discovery_declares_optional_tie_inventory(tmp_path):
    reference = run_process(tmp_path, "reference", "--command", "pcb inspect")
    schema = reference["schemas"]["pcb_info"]
    assert "tie_inventory" in schema["observation_shape"]
    assert "tie_inventory" in schema["observation_optional_fields"]
    boundary = schema["observation_tie_inventory_boundary"]
    assert boundary["complete_material_proven"] is False
    assert "does not prove absence" in boundary["empty_meaning"]


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("ties_path", "two_matching_complete_reads"),
        ("ties_empty", "two_matching_complete_reads"),
        ("ties_width_error", "unverified"),
        ("ties_cutout_error", "unverified"),
        ("ties_item_error", "unverified"),
        ("ties_collection_error", "unverified"),
        ("ties_drift", "unverified"),
    ],
)
def test_fresh_public_live_ties_keep_core_geometry_and_physical_boundaries(
    tmp_path, fault, expected
):
    data = run_process(
        tmp_path,
        "pcb",
        "inspect",
        "--project",
        str(tmp_path / "owned.pcb"),
        "--live-geometry",
        "--pid",
        "4242",
        synthetic=True,
        fault=fault,
    )
    observation = data["observation"]
    assert observation["consistency"] == "two_matching_live_reads"
    assert observation["plane_state"]["generated"]
    ties = observation["tie_inventory"]
    assert ties["consistency"] == expected
    assert ties["evidence_boundary"]["complete_material_proven"] is False
    assert ties["evidence_boundary"]["pad_contact_proven"] is False
    assert observation["persistence"] == "unknown"
    assert observation["engineering_write_dispatched"] is False
    if fault == "ties_path":
        path = ties["collection"]["items"][0]["geometry"]
        assert path["is_closed"] is False and path["is_arced"] is True
        assert path["line_width_mm"] == 0.254 and path["points_xyr_mm"][1][2] == 0.5
        assert ties["collection"]["items"][0]["native_layer"] == 7
    if fault == "ties_empty":
        assert ties["collection"]["count"] == 0
        assert "does not prove absence" in ties["evidence_boundary"]["empty_meaning"]
