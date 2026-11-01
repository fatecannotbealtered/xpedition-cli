"""R15/052 owned RC originals, historical parsing only; no CAD or new DRC Go."""

import hashlib
from pathlib import Path

import pytest

from xpedition_cli import drc_engine as D


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_real_052_record_families_raw_fields_and_no_fabricated_execution():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_partial_parser_never_extends_hazard_mapping_even_with_identical_pad():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_report_tree_is_json_stable_and_retains_uninterpreted_fields():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_nonfinite_uninterpreted_fields_cannot_escape_json_boundary():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_actual_partial_shape_failure_paths_are_rejected():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_different_partial_record_and_object_positions_remain_distinct_not_matched():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_manifest_and_requested_configuration_are_not_optional():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_live_freshness_remains_required_for_same_historical_bytes():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_untrusted_manifest_completion_flags_cannot_upgrade_historical_parse():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_file_or_manifest_drift_during_parsing_refuses_result():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_transient_A_B_A_path_change_cannot_relabel_consumed_B_as_manifest_A():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


def test_pure_report_parser_consumes_same_captured_outputs_and_selected_inputs_without_IO(
    tmp_path, monkeypatch
):
    from test_drc_engine import report_files

    paths = report_files(tmp_path / "owned/PCB/board.pcb")
    selected = {}
    for key in ("rules", "elements"):
        path = tmp_path / ("selected-" + key)
        path.write_bytes(paths["effective_" + key].read_bytes())
        selected[key] = D.file_record(path)
    captured = {key: D._read_contents(path) for key, path in paths.items()}
    selected_captured = {key: D._selected_contents(row) for key, row in selected.items()}

    def no_open(*args, **kwargs):
        pytest.fail("The pure report/configuration parser must not reopen a source path")

    monkeypatch.setattr(Path, "open", no_open)
    monkeypatch.setattr(Path, "read_bytes", no_open)
    report = D._parse_report_contents(
        captured,
        selected=selected,
        selected_snapshots=selected_captured,
        engine_version="MGCPCBEngines.DRC.151",
    )
    assert report["count"] == 1
    assert report["configuration_binding"]["verified"]
    for key, value in captured.items():
        assert report["files"][key]["sha256"] == hashlib.sha256(value.data).hexdigest()
        assert report["files"][key]["bytes"] == len(value.data)
    for key, value in selected_captured.items():
        actual = report["configuration_binding"]["comparisons"][key]
        assert actual["selected_sha256"] == hashlib.sha256(value.data).hexdigest()
        assert (
            actual["effective_sha256"]
            == hashlib.sha256(captured["effective_" + key].data).hexdigest()
        )


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_public_result_history_mode_is_discoverable_and_never_dispatches():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_real_process_reads_052_as_history_and_preserves_input_bytes():
    """Original historical coverage is not reproduced in this snapshot."""
    pass
