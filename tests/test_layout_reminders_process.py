"""Actual public CLI processes; synthetic geometry, never real CAD acceptance."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_main():
    import pytest
    from test_driver_v2 import _scope, _worker_fixture
    from test_layout_reminders import model, text

    from xpedition_cli import board_view, driver, layout_reminders
    from xpedition_cli import native_com_adapter as native
    from xpedition_cli.cli import common, pcb
    from xpedition_cli.errors import CLIError
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_LAYOUT_PROCESS_FIXTURE"])
    mode = os.environ.get("XPEDITION_LAYOUT_PROCESS_CASE", "overlap")
    data = model(text(), text((3, 2, 5, 3), uid="second", refdes="J1"))
    if mode == "rotated":
        data["texts"][0]["orientation"] = 270
    elif mode == "different_side":
        data["texts"][1].update(observed_side="bottom", side="bottom")
    elif mode == "different_layer":
        data["texts"][1]["fabrication_layer"] = "assembly"
    elif mode == "disjoint":
        data["texts"][1]["extents"] = [6, 2, 8, 3]
    elif mode == "missing_envelope":
        del data["texts"][1]["extents"]
    elif mode == "unsupported_font":
        data["texts"][1].update(strokes=[], stroke_source=None)
    elif mode == "unknown_orientation":
        data["texts"][1]["orientation"] = None
    original = copy.deepcopy(data)
    patches = pytest.MonkeyPatch()
    _scope(folder, patches)
    board = folder / "owned.pcb"
    if mode.startswith("native_"):
        from layout_native_inputs import bind_source

        bind_source(patches, board, mode.removeprefix("native_"))

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            assert data == original
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "board_geometry":
                assert not params.get("output"), "preview must not draw or write an artifact"
                if mode == "unit_not_mils" or mode.startswith("native_"):
                    try:
                        return native._board_geometry(params, object())
                    except native.AdapterError as error:
                        raise CLIError(error.code, str(error), error.details) from error
                return {
                    "model": copy.deepcopy(data),
                    "pcb": str(board),
                    **layout_reminders.report(data),
                }
            if method == "board_view_state":
                return {
                    "model": copy.deepcopy(data),
                    "pcb": str(board),
                    "scheme_plan": board_view.scheme_plan(board, 4, params, native),
                    **layout_reminders.report(data),
                }
            raise AssertionError("No native mutation or artifact generation permitted: " + method)

    backend = Backend()
    patches.setattr(driver, "NativeBackend", lambda: backend)
    patches.setattr(pcb, "native", lambda: backend)
    patches.setattr(pcb, "reader", lambda options: backend)
    patches.setattr(common, "reader", lambda options: backend)
    if mode == "unit_not_mils":
        from types import SimpleNamespace

        document = SimpleNamespace(CurrentUnit=4, FullName=str(board.resolve()))
        app = SimpleNamespace(ActiveDocument=document)
        patches.setattr(native, "_application", lambda *args, **kwargs: app)
        patches.setattr(native, "_open_layout_document", lambda *args: (document, []))
        patches.setattr(native, "_licensed_document", lambda *args: document)
        patches.setattr(native, "application_identity", lambda *args: {"pid": 4242})
        patches.setattr(
            native,
            "_board_model",
            lambda *args, **kwargs: pytest.fail("unit guard must precede model"),
        )
    try:
        return main()
    finally:
        patches.undo()


def run_process(folder, *args, case=None, synthetic=True, expect_ok=True):
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(folder / "config"),
        "XPEDITION_NATIVE_COMMAND": str(folder / "no-live-adapter.exe"),
        "XPEDITION_LAYOUT_PROCESS_FIXTURE": str(folder),
        "XPEDITION_LAYOUT_PROCESS_CASE": case or "overlap",
    }
    if synthetic:
        bootstrap = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'tests')!r}); "
            "from test_layout_reminders_process import _synthetic_main; "
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
    assert (result.returncode == 0) is expect_ok and body["ok"] is expect_ok, (body, result.stderr)
    return body["data"] if expect_ok else body["error"]


@pytest.mark.parametrize("command", ["snapshot", "render", "view"])
@pytest.mark.parametrize(
    "case",
    [
        "overlap",
        "rotated",
        "disjoint",
        "different_side",
        "different_layer",
        "missing_envelope",
        "unsupported_font",
        "unknown_orientation",
    ],
)
def test_public_preview_exposes_actual_reminders_and_missing_coverage_before_drawing(
    tmp_path, command, case
):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic board, never opened by CAD")
    config = tmp_path / "Config"
    config.mkdir()
    (config / "All On.dcs").write_text("synthetic scheme")
    output = tmp_path / ("new.json" if command == "snapshot" else "new.png")
    args = ["pcb", command, "--project", str(board), "--output", str(output)]
    if command == "view":
        args += ["--scheme", "Loc: All On"]
    result = run_process(tmp_path, *args, "--dry-run", case=case)
    preview = result["preview"]["extensions"]["legacy_preview"]
    coverage = preview["layout_reminder_coverage"]
    assert coverage["empty_warnings_mean_layout_clear"] is False
    assert coverage["manual_visual_review_required"] is True
    assert not output.exists()
    assert board.read_bytes() == b"synthetic board, never opened by CAD"
    assert any(r["rule"] == "text_text" for r in coverage["checked_rules"])
    if case in {"overlap", "rotated"}:
        warning = next(
            w for w in preview["layout_reminders"] if w["kind"] == "projected_text_text_overlap"
        )
        assert warning["objects"][0]["uid"] == "text-1"
        assert warning["advisory_proximity_mm"] == 0.3
        assert coverage["status"] == "checked_projected_subset"
    else:
        assert preview["layout_reminders"] == []
    if case in {"missing_envelope", "unsupported_font", "unknown_orientation"}:
        assert coverage["status"] == "partial"
        assert coverage["unreviewed_objects"][0]["object"]["uid"] == "second"
        assert coverage["unreviewed_objects"][0]["reasons"]


@pytest.mark.parametrize("command", ["snapshot", "render"])
@pytest.mark.parametrize(
    "case",
    [
        "empty_trace",
        "trace_geometry_getter_failed",
        "empty_graphic",
        "graphic_geometry_getter_failed",
        "trace_layer_missing",
        "trace_layer_failed",
        "trace_layer_zero",
        "trace_layer_invalid",
        "trace_layer_large",
        "layer_count_missing",
        "layer_count_failed",
        "layer_count_zero",
        "layer_count_invalid",
        "first_trace_unreadable",
        "board_cutout",
        "board_cutouts_failed",
    ],
)
def test_public_preview_uses_native_shape_conversion_and_reports_real_coverage(
    tmp_path, command, case
):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"owned synthetic native input, never opened by CAD")
    output = tmp_path / ("new.json" if command == "snapshot" else "new.png")
    incomplete_material = case not in {"board_cutout", "board_cutouts_failed"}
    result = run_process(
        tmp_path,
        "pcb",
        command,
        "--project",
        str(board),
        "--output",
        str(output),
        "--dry-run",
        case="native_" + case,
        expect_ok=not incomplete_material,
    )
    if incomplete_material:
        assert result["code"] == "E_UNSUPPORTED"
        assert result["details"]["dispatch_state"] == "not_started"
        assert not output.exists()
        assert board.read_bytes() == b"owned synthetic native input, never opened by CAD"
        return
    preview = result["preview"]["extensions"]["legacy_preview"]
    coverage = preview["layout_reminder_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["unreviewed_objects"]
    assert coverage["empty_warnings_mean_layout_clear"] is False
    assert not output.exists()
    assert case.startswith("board_cutout")
    assert coverage["board_edge_coverage"]["complete"] is False
    assert board.read_bytes() == b"owned synthetic native input, never opened by CAD"


def test_public_discovery_names_pre_draw_coverage_limits(tmp_path):
    for command in ("pcb snapshot", "pcb render", "pcb view"):
        result = run_process(tmp_path, "reference", "--command", command, synthetic=False)
        assert "layout_reminder_coverage" in json.dumps(result)


@pytest.mark.parametrize("command", ["snapshot", "render"])
def test_public_preview_rejects_wrong_native_unit_before_drawing(tmp_path, command):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic, unit stays mm")
    output = tmp_path / ("new.json" if command == "snapshot" else "new.png")
    error = run_process(
        tmp_path,
        "pcb",
        command,
        "--project",
        str(board),
        "--output",
        str(output),
        "--dry-run",
        case="unit_not_mils",
        expect_ok=False,
    )
    assert error["code"] == "E_UNSUPPORTED"
    assert error["details"]["unit_not_changed"] is True
    assert error["details"]["dispatch_state"] == "not_started"
    assert not output.exists()
