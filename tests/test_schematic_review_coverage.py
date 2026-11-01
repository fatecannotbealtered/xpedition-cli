"""Schematic preflight honesty through native-shape reads and public CLI processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as native

ROOT = Path(__file__).resolve().parents[1]


class Collection(list):
    @property
    def Count(self):  # noqa: N802
        return len(self)

    def Item(self, index):  # noqa: N802
        return self[index - 1]


def native_shape_snapshot(*, include_sheet_census=False):
    """Actual adapter conversion omits attribute graphics despite readable values."""
    pin = SimpleNamespace(Number="1", GetLocation=SimpleNamespace(X=100, Y=200))
    component = SimpleNamespace(
        Refdes="C1",
        UID="Schematic1:1:C1",
        Orientation=1,
        GetLocation=lambda: SimpleNamespace(X=100, Y=200),
        Attributes=Collection([SimpleNamespace(Name="Value", Value="100n")]),
        GetConnections=lambda: Collection(
            [SimpleNamespace(CompPin=pin, Net=SimpleNamespace(Name="CH_A"))]
        ),
    )
    app = SimpleNamespace(
        GetActiveDesign=lambda: "Board1",
        DesignComponents=lambda *_: Collection([component]),
        DesignNets=lambda *_: Collection([]),
        SchematicSheetDocuments=lambda: Collection([]),
    )
    if include_sheet_census:
        # PDF's source binding needs an actual getter-shaped sheet census. It
        # still supplies no native font/text envelopes for drawing acceptance.
        documents = Collection([])
        documents.GetAvailableSchematics = lambda: SimpleNamespace(
            GetCount=lambda: 1, GetItem=lambda index: "Schematic1"
        )
        documents.GetAvailableSheets = lambda schematic: SimpleNamespace(
            GetCount=lambda: 1, GetItem=lambda index: "1"
        )
        app.SchematicSheetDocuments = lambda: documents
    return native._designer_snapshot(app, {})


def test_native_shape_edit_preview_refuses_incomplete_protection_without_writing(
    cli, adapter, project, tmp_path
):
    snapshot = native_shape_snapshot()
    assert snapshot["components"][0]["value"] == "100n"
    assert snapshot["components"][0]["rotation"] == 1
    assert "attribute_geometry" not in snapshot["components"][0]
    adapter.on("snapshot", snapshot)
    changes = tmp_path / "eco.json"
    changes.write_text(
        json.dumps(
            {
                "operations": [
                    {"type": "set_property", "refdes": "C1", "name": "Value", "value": "220n"}
                ]
            }
        ),
        encoding="utf-8",
    )
    code, payload = cli(
        "schematic", "edit", "--project", str(project), "--file", str(changes), "--dry-run"
    )
    assert code == 2 and payload["ok"] is False, payload
    assert payload["error"]["code"] == "E_VALIDATION"
    details = payload["error"]["details"]
    assert details["stage"] == "ordinary_edit_preflight"
    assert details["write_attempted"] is details["native_request_dispatched"] is False
    assert details["validation"]["valid"] is False
    assert "confirm_token" not in json.dumps(payload)
    assert "apply_changeset" not in adapter.methods()


def test_native_shape_advisory_retains_unobserved_layout_risks():
    from xpedition_cli.schematic_review import native_edit

    review = native_edit(
        native_shape_snapshot(),
        [
            {"type": "set_property", "refdes": "C1", "name": "Value", "value": "220n"},
        ],
    )
    assert review["status"] == "not_checked"
    assert review["empty_findings_mean_layout_clear"] is False
    assert review["findings"] == []
    item = next(row for row in review["unreviewed_objects"] if row["object"].get("refdes") == "C1")
    assert "attribute_envelope_not_observed" in item["reasons"]
    assert item["affected_rules"]


@pytest.mark.parametrize("kind", ["move_component", "delete_component", "disconnect"])
def test_native_edit_lists_each_affected_object_and_does_not_invent_geometry(kind):
    from xpedition_cli.schematic_review import native_edit

    op = {"type": kind, "refdes": "C1", "x": 200, "y": 200, "pin": "C1.1"}
    review = native_edit(native_shape_snapshot(), [op])
    assert review["checked_rules"] == []
    assert review["findings"] == []
    assert review["unreviewed_objects"][0]["object"]["refdes"] == "C1"
    assert review["unreviewed_objects"][0]["operation_type"] == kind
    assert "text_direction" in review["unreviewed_objects"][0]["affected_rules"]
    assert review["manual_full_sheet_review_required"] is True


def test_planned_font_and_per_object_unmodelled_graphics_are_explicit():
    from xpedition_cli.schematic_layout import Plan
    from xpedition_cli.schematic_review import planned

    plan = Plan(
        ops=[
            {"op": "open_sheet", "number": 1},
            {
                "op": "place_part",
                "refdes": "C1",
                "symbol": "CAP",
                "library": "Owned",
                "x": 200,
                "y": 200,
                "orientation": 1,
            },
            {"op": "place_symbol", "symbol": "gnd", "library": "Globals", "x": 500, "y": 500},
        ],
        symbols={"CAP": "V 53\nl 2 0 0 20 0\nc 0 0 4\nU 0 0 8 1 3 3 DEVICE=100n\nE\n"},
        sheets=[{"number": 1}],
        sheet_size="B",
    )
    review = planned(plan)
    assert review["status"] == "partial" and review["findings"] == []
    assert review["empty_findings_mean_layout_clear"] is False
    assert review["text_envelope_basis"] == "estimated_character_width_not_native_font_extents"
    assert {row["rule"] for row in review["checked_rules"]} == {
        "DS-07",
        "DS-08",
        "DS-15",
        "DS-16",
        "DS-17",
    }
    capacitor = next(
        row for row in review["unreviewed_objects"] if row["object"].get("refdes") == "C1"
    )
    assert capacitor["object"]["operation_index"] == 1
    assert capacitor["unmodelled_records"] == [
        {"line": 3, "record": "c", "reason": "record_not_modelled"},
        {"line": 4, "record": "U", "reason": "attribute_rotation_not_modelled"},
    ]
    ground = next(
        row for row in review["unreviewed_objects"] if row["object"].get("symbol") == "gnd"
    )
    assert ground["reasons"] == ["stock_symbol_standin_not_actual_native_geometry"]


def test_build_preflight_and_result_keep_the_same_review(cli, adapter, project, tmp_path):
    source = ROOT / "examples/demo-sensor-board.json"
    design = tmp_path / "design.json"
    design.write_bytes(source.read_bytes())
    adapter.on("draw", {"applied": True, "netlist": {"matches": True}})
    args = ["schematic", "build", "--project", str(project), "--design", str(design)]
    code, preview = cli(*args, "--dry-run")
    assert code == 0, preview
    before = preview["data"]["preview"]["drawing_review"]
    code, actual = cli(*args, "--confirm", preview["data"]["confirm_token"], "--dangerous")
    assert code == 0, actual
    assert actual["data"]["drawing_review"] == before
    assert "drawing_review" not in adapter.last("draw")


def test_incomplete_shape_cannot_gain_edit_confirmation_from_saved_flag(
    cli, adapter, project, tmp_path
):
    before = native_shape_snapshot()
    adapter.on("snapshot", before)
    adapter.on("apply_changeset", {"applied": [{"type": "set_property"}], "saved": True})
    changes = tmp_path / "eco.json"
    changes.write_text(
        json.dumps(
            {
                "operations": [
                    {"type": "set_property", "refdes": "C1", "name": "Value", "value": "220n"}
                ]
            }
        ),
        encoding="utf-8",
    )
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    code, preview = cli(*args, "--dry-run")
    assert code == 2 and preview["ok"] is False, preview
    code, actual = cli(*args, "--confirm", "unissued-offline-token")
    assert code == 2 and actual["ok"] is False, actual
    for body in (preview, actual):
        assert body["error"]["details"]["stage"] == "ordinary_edit_preflight"
        assert body["error"]["details"]["validation"]["valid"] is False
        assert body["error"]["details"]["write_attempted"] is False
    assert "apply_changeset" not in adapter.methods()


def test_saved_flag_does_not_supply_independent_edit_durability(project):
    import copy

    from test_schematic_edit_protection import _snapshot, _value

    from xpedition_cli.cli.schematic import verify_ordinary_edit_durability

    before = _snapshot()
    after = copy.deepcopy(before)
    _value(after, "22K")
    result = verify_ordinary_edit_durability(
        project,
        before,
        after,
        [{"type": "set_property", "refdes": "R1", "name": "Value", "value": "22K"}],
        {"saved": True},
    )
    assert result["valid"] is result["complete"] is False
    assert result["evidence_present"] is False


def _synthetic_main():
    from fakes import FakeAdapter
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli.backends import native_xpedition
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_SCHEMATIC_REVIEW_FIXTURE"])
    patches = pytest.MonkeyPatch()
    _scope(folder, patches)
    binary = folder / "fixture-adapter.exe"
    binary.write_bytes(b"not executable; subprocess boundary is synthetic")
    patches.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    exporting = sys.argv[1:3] == ["schematic", "export"]
    home = folder / "licensed-fixture"
    producer = home / "common/win64/bin/sch2pdf.exe"
    producer.parent.mkdir(parents=True)
    producer.write_bytes(b"synthetic converter identity; never executed")
    fake = (
        FakeAdapter()
        .on("snapshot", native_shape_snapshot(include_sheet_census=exporting))
        .on(
            "identity",
            {
                "application": {
                    "pid": 4242,
                    "document": {
                        "open": True,
                        "path": str((folder / "Owned.prj").resolve()),
                        "status": "observed",
                    },
                },
                "adapter": _worker_fixture(),
            },
        )
    )
    patches.setattr(
        native_xpedition.NativeBackend,
        "status",
        lambda self: {
            "available": True,
            "automation_command": str(binary),
            "sdd_home": str(home),
        },
    )
    patches.setattr(native_xpedition.subprocess, "run", fake.run)
    try:
        return main()
    finally:
        (folder / "process-events.json").write_text(json.dumps(fake.calls), encoding="utf-8")
        patches.undo()


def run_process(folder, *args, synthetic=False, expected_error=None):
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(folder / "config"),
        "XPEDITION_NATIVE_COMMAND": str(folder / "no-live-native-adapter.exe"),
        "XPEDITION_SCHEMATIC_REVIEW_FIXTURE": str(folder),
    }
    if synthetic:
        bootstrap = (
            f"import sys; sys.path.insert(0, {str(ROOT / 'tests')!r}); "
            "from test_schematic_review_coverage import _synthetic_main; "
            "raise SystemExit(_synthetic_main())"
        )
        prefix = [sys.executable, "-c", bootstrap]
    else:
        prefix = [sys.executable, "-m", "xpedition_cli"]
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
    if expected_error is not None:
        assert result.returncode == 9 and body["ok"] is False, (body, result.stderr)
        assert body["error"]["code"] == expected_error
        return body["error"]
    assert result.returncode == 0 and body["ok"], (body, result.stderr)
    return body["data"]


@pytest.mark.parametrize("command", ["edit", "view", "export"])
def test_public_process_refuses_pid_path_fixture_without_owned_connection(tmp_path, command):
    project = tmp_path / "Owned.prj"
    project.write_text("owned fixture", encoding="utf-8")
    operations = tmp_path / "eco.json"
    operations.write_text(
        json.dumps(
            {
                "operations": [
                    {"type": "set_property", "refdes": "C1", "name": "Value", "value": "220n"}
                ]
            }
        ),
        encoding="utf-8",
    )
    args = ["schematic", command, "--project", str(project)]
    if command == "edit":
        args += ["--file", str(operations)]
    elif command == "export":
        args += ["--output", str(tmp_path / "whole.pdf")]
    error = run_process(
        tmp_path, *args, "--dry-run", synthetic=True, expected_error="E_HUMAN_REQUIRED"
    )
    details = error["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["write_attempted"] is details["native_request_dispatched"] is False
    calls = json.loads((tmp_path / "process-events.json").read_text(encoding="utf-8"))
    assert {call["method"] for call in calls} <= {"identity", "snapshot"}
    assert not (tmp_path / "whole.pdf").exists()


@pytest.mark.parametrize("command", ["edit", "view", "export"])
def test_shape_review_algorithms_expose_unchecked_scope_separately_from_cli_admission(command):
    from xpedition_cli.schematic_review import native_drawing, native_edit, unobserved_drawing

    snapshot = native_shape_snapshot(include_sheet_census=command == "export")
    review = (
        native_edit(
            snapshot,
            [
                {"type": "set_property", "refdes": "C1", "name": "Value", "value": "220n"},
            ],
        )
        if command == "edit"
        else native_drawing(snapshot)
        if command == "view"
        else unobserved_drawing("public preview has no verified same-owner source")
    )
    assert review["status"] == "not_checked" and review["findings"] == []
    assert review["empty_findings_mean_layout_clear"] is False
    if command == "export":
        assert review["basis"] == "not_observed" and review["unreviewed_objects"] == []
        assert review["object_counts"]["snapshot_components"] is None
        assert any(row["collection"] == "components" for row in review["unreviewed_collections"])
    else:
        assert review["basis"] == "native_snapshot_structural_fields_only"
        assert any(row["object"].get("refdes") == "C1" for row in review["unreviewed_objects"])
    if command != "edit":
        assert all(not row["enumeration_complete"] for row in review["unreviewed_collections"])


def test_pdf_plan_pins_declared_converter_and_sheet_census_without_claiming_owner(tmp_path):
    from xpedition_cli.schematic_pdf_artifacts import expectation

    project = tmp_path / "Owned.prj"
    project.write_bytes(b"synthetic source, not an editable native project")
    producer = tmp_path / "common/win64/bin/sch2pdf.exe"
    producer.parent.mkdir(parents=True)
    producer.write_bytes(b"synthetic converter identity; never executed")
    output = tmp_path / "whole.pdf"
    expected = expectation(
        {"project": str(project), "output": str(output)},
        {"native": native_shape_snapshot(include_sheet_census=True)},
        producer_executable=producer,
    )
    assert expected["sheet_census"] == [
        {"schematic": "Schematic1", "number": 1, "printed": "Schematic1 Sheet 1"}
    ]
    assert expected["producer"]["path"] == str(producer.resolve())
    assert expected["producer"]["bytes"] == producer.stat().st_size
    assert expected["native_application"] is None and expected["generation_id"] is None
    assert not output.exists()


def test_public_process_plan_image_preflight_exposes_estimated_coverage(tmp_path):
    source = ROOT / "examples/demo-sensor-board.json"
    design = tmp_path / "design.json"
    design.write_bytes(source.read_bytes())
    output = tmp_path / "preview.png"
    data = run_process(
        tmp_path,
        "schematic",
        "plan",
        "preview",
        "--design",
        str(design),
        "--output",
        str(output),
        "--dry-run",
        synthetic=True,
    )
    review = data["preview"]["extensions"]["legacy_preview"]["design_preview"]["drawing_review"]
    assert review["basis"] == "planned_projection"
    assert review["text_envelope_basis"] == "estimated_character_width_not_native_font_extents"
    assert review["unreviewed_objects"] and not output.exists()


@pytest.mark.parametrize(
    "command",
    [
        "schematic plan preview",
        "schematic build",
        "schematic edit",
        "schematic view",
        "schematic export",
    ],
)
def test_public_process_reference_discovers_preflight_limits(tmp_path, command):
    data = run_process(tmp_path, "reference", "--command", command)
    assert "drawing_review" in data["command"]["description"]
    shape = data["schemas"][data["command"]["output_schema"]]
    assert "drawing_review" in shape["fields"]
    assert "unreviewed_objects" in shape["drawing_review_shape"]
    if command in {"schematic plan preview", "schematic build"}:
        assert shape["design_file_spacing"]["path"] == "sheets[].blocks[].pitch"
        design = next(item for item in data["command"]["params"] if item["name"] == "design")
        assert "pitch" in design["description"]
    if command == "schematic view":
        assert "capture_context" in shape["fields"]
        assert "active_view_hwnd" in shape["capture_context_shape"]
        assert "without --output" in data["command"]["description"]
        assert "incomplete" in shape["without_output"]
        assert any("--output" in example for example in data["command"]["examples"])
    assert len(run_process(tmp_path, "reference", "--index")["commands"]) == 65


def test_capture_discovery_declares_source_context_and_bounded_independent_acceptance():
    from xpedition_cli.cli.registry import by_path
    from xpedition_cli.reference_data import SCHEMAS

    command = by_path()["schematic view"]
    shape = SCHEMAS[command.output_schema]
    assert "capture_context" in shape["fields"]
    assert shape["capture_context_shape"] == [
        "project",
        "sheet",
        "pid",
        "executable",
        "application_hwnd",
        "active_view_hwnd",
        "window_hwnd",
        "same_root_verified",
        "foreground_verified",
    ]
    assert "not by itself independent acceptance" in shape["capture_verification_scope"]
    assert "incomplete" in shape["without_output"]
    assert "native ERC" in shape["capture_not_checked"]
    assert any("--output" in example for example in command.examples)
    output = next(item for item in command.params if item.name == "output")
    assert output.required is False and "recommended" in output.description
