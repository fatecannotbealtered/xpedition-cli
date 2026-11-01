"""The mode prerequisite must be visible before an engineer confirms routing."""

import json
import subprocess
import sys
from pathlib import Path

from xpedition_cli.reference_data import SCHEMAS

ROOT = Path(__file__).resolve().parents[1]


def test_public_process_reference_discloses_gui_mode_prerequisite_and_separate_ui_layer_put():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xpedition_cli",
            "reference",
            "--command",
            "pcb routing auto",
            "--compact",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["ok"]
    assert envelope["data"]["driver_profile"]["schema_version"] == "2.0"
    schema = envelope["data"]["schemas"]["pcb_route"]
    boundary = schema["bounded_general_configuration_boundary"]
    assert "Gui.ActiveMode PROPERTYGET17" in boundary
    assert "Route1" in boundary
    assert "Select the normal Route working mode in the actual Layout GUI first" in boundary
    assert "not a guarantee" in boundary
    assert "Gui.ActiveRouteLayer" in schema["bounded_general_ui_layer_selection_boundary"]
    assert schema["bounded_general_native_dispatch_supported"] is False
    assert "can_apply=false" in schema["bounded_general_selector_boundary"]


def test_static_public_schema_matches_the_actual_general_mode_boundary():
    registry = json.loads((ROOT / "registry.json").read_text("utf-8"))
    assert (
        registry["schemas"]["pcb_route"]["bounded_general_configuration_boundary"]
        == SCHEMAS["pcb_route"]["bounded_general_configuration_boundary"]
    )


def test_public_inspect_reference_discloses_selected_object_observation_scope():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xpedition_cli",
            "reference",
            "--command",
            "pcb inspect",
            "--compact",
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    schemas = json.loads(result.stdout)["data"]["schemas"]
    matches = [v for v in schemas.values() if "observation_native_general_shape" in v]
    assert len(matches) == 1
    assert {"selection", "selection_after", "selection_consistency"} <= set(
        matches[0]["observation_native_general_shape"]
    )
    assert "after the material read" in matches[0]["observation_native_general_boundary"]
