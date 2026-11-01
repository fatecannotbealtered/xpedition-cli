"""File-only DRC evidence checks; no SDK/spec import, native engine or CAD call."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from drc_fakes import write_review_evidence

from xpedition_cli import drc_engine as drc


def reports(tmp_path):
    board = tmp_path / "PCB/owned.pcb"
    board.parent.mkdir(parents=True)
    board.write_bytes(b"synthetic board")
    paths = drc.outputs(board)
    values = {
        "effective_rules": '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.UNROUTEDPINS YES\n',
        "effective_elements": '.FILETYPE BATCH_DRC_E2E_FILE\n.VERSION "2.02"\n',
        "report": "DRC\n===\n检查未布线的管脚 : YES\nTotal DRC Hazards Found : 1\n",
        "violations": '.FILETYPE DRCFILE\n.VERSION "2002"\n.DATE "Today"\n.TIME "Now"\n'
        ".UNROUTED_NET\n..XY (1000000, 2000000)\n..OBJECT\n...PAD PART\n"
        '....REF_DES "R1"\n....NET "QA_SIGNAL"\n....XY (1000000, 2000000)\n',
    }
    for role, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(values[role], encoding="utf-8")
    return board, paths


def test_consumed_file_bytes_and_generation_are_the_recorded_source(tmp_path):
    board, paths = reports(tmp_path)
    result = drc.read_reports(paths, {}, 1, expected_board=board)
    assert result["count"] == 1 and result["parsed_content_complete"]
    assert result["configuration_binding"]["verified"] is False
    for role, row in result["files"].items():
        assert row == drc.file_record(paths[role])
        assert row["file_identity"]["inode"] == str(paths[role].stat().st_ino)


@pytest.mark.parametrize("role", ["report", "violations", "effective_rules", "effective_elements"])
def test_live_output_location_is_bound_to_each_role_and_board(tmp_path, role):
    board, paths = reports(tmp_path)
    foreign = tmp_path / paths[role].name
    foreign.write_bytes(paths[role].read_bytes())
    paths[role] = foreign
    with pytest.raises(ValueError, match="location differs"):
        drc.read_reports(paths, {}, 1, expected_board=board)


@pytest.mark.parametrize("fault", ["missing", "extra", "alias"])
def test_output_role_inventory_is_exact(tmp_path, fault):
    board, paths = reports(tmp_path)
    if fault == "missing":
        del paths["report"]
    elif fault == "extra":
        paths["extra"] = paths["report"]
    else:
        paths["report"] = paths["violations"]
    with pytest.raises(ValueError, match="roles do not match|location differs"):
        drc.validate_output_paths(paths, board)


@pytest.mark.parametrize("role", ["report", "violations", "effective_rules", "effective_elements"])
def test_late_file_change_after_parsing_cannot_certify_the_set(tmp_path, monkeypatch, role):
    board, paths = reports(tmp_path)
    parse = drc._parse_report_contents

    def mutate_after_capture(*args, **kwargs):
        result = parse(*args, **kwargs)
        paths[role].write_bytes(paths[role].read_bytes() + b"\n")
        return result

    monkeypatch.setattr(drc, "_parse_report_contents", mutate_after_capture)
    with pytest.raises(ValueError, match="changed during report-set capture"):
        drc.read_reports(paths, {}, 1, expected_board=board)


def test_named_file_identity_must_still_match_the_open_handle(tmp_path, monkeypatch):
    path = tmp_path / "report.txt"
    path.write_bytes(b"same bytes")
    original_stat = Path.stat

    def changed_identity(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        if self == path and kwargs.get("follow_symlinks") is not False:
            return SimpleNamespace(
                **{
                    name: getattr(info, name)
                    for name in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev")
                },
                st_ino=info.st_ino + 1,
            )
        return info

    monkeypatch.setattr(Path, "stat", changed_identity)
    with pytest.raises(ValueError, match="changed while capturing"):
        drc._read_contents(path)


def test_windows_path_ctime_semantics_do_not_replace_file_identity_checks(tmp_path, monkeypatch):
    path = tmp_path / "report.txt"
    path.write_bytes(b"same bytes")
    original_stat = Path.stat

    def other_ctime(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        if self == path and kwargs.get("follow_symlinks") is not False:
            fields = ("st_size", "st_mtime_ns", "st_dev", "st_ino")
            return SimpleNamespace(**{k: getattr(info, k) for k in fields}, st_ctime_ns=0)
        return info

    monkeypatch.setattr(Path, "stat", other_ctime)
    assert drc._read_contents(path).data == b"same bytes"


def test_oversized_file_refuses_before_unbounded_read(tmp_path, monkeypatch):
    path = tmp_path / "report.txt"
    path.write_bytes(b"12345")
    monkeypatch.setattr(drc, "MAX_NATIVE_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="byte limit"):
        drc._read_contents(path)


def test_symlink_report_refuses_instead_of_following_another_output(tmp_path):
    path, link = tmp_path / "report.txt", tmp_path / "alias.txt"
    path.write_bytes(b"native report")
    try:
        link.symlink_to(path)
    except OSError as error:
        pytest.skip("Symlink creation unavailable: " + type(error).__name__)
    with pytest.raises(ValueError, match="reparse"):
        drc.file_record(link)


def test_reparse_directory_is_rejected_before_read(tmp_path, monkeypatch):
    path = tmp_path / "Config/BatchDrc.dat"
    original_lstat = Path.lstat

    def reparse_parent(self):
        if self == path.parent:
            return SimpleNamespace(st_mode=0, st_file_attributes=0x400)
        return original_lstat(self)

    monkeypatch.setattr(Path, "lstat", reparse_parent)
    with pytest.raises(ValueError, match="reparse"):
        drc.file_record(path)


def test_stale_or_unchanged_prior_outputs_still_refuse(tmp_path):
    board, paths = reports(tmp_path)
    before = {key: drc.file_record(path) for key, path in paths.items()}
    with pytest.raises(ValueError, match="No fresh"):
        drc.read_reports(paths, before, 1, expected_board=board)
    for path in paths.values():
        os.utime(path, ns=(1, 1))
    with pytest.raises(ValueError, match="No fresh"):
        drc.read_reports(paths, {}, 2, expected_board=board)


def test_all_declared_lifecycle_logs_now_require_byte_observation(tmp_path):
    effects = drc.planned_side_effects(tmp_path / "PCB/board.pcb")
    assert len(effects) == 10
    assert all(row["observe_bytes"] is True for row in effects)


@pytest.mark.parametrize("display", ["0th", "4.9th", "0.12446mm", "4.9", "nanth"])
def test_annular_display_never_establishes_unknown_native_serialization(display):
    prefix = '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.BASE_UNITS 0\n.MINIMUMANNULARRING YES\n'
    selected = drc.R.parse_records(prefix + '.MINIMUMANNULARRINGVALUE "124460"\n')
    effective = drc.R.parse_records(prefix + '.MINIMUMANNULARRINGVALUE "4.9"\n')
    result = drc.annular_unit_observation(
        "环形距离: " + display, selected, effective, engine_version="MGCPCBEngines.DRC.151"
    )
    assert result["status"] == "unverified"
    assert result["file_to_display_binding_verified"] is False
    assert result["can_authorize_rule_write"] is False
    assert result["selected_raw_fields"]["MINIMUMANNULARRINGVALUE"] == [["124460"]]
    assert result["effective_raw_fields"]["MINIMUMANNULARRINGVALUE"] == [["4.9"]]
    assert result["display_observations"][0]["raw"] == display
    if display in {"4.9th", "0.12446mm"}:
        assert result["display_observations"][0]["value_mm_fraction"] == [6223, 50000]


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_historical_native_annular_zero_label_is_retained_without_new_go():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


def annular_inputs(tmp_path, paths):
    prefix = '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.BASE_UNITS 0\n.MINIMUMANNULARRING YES\n'
    paths["effective_rules"].write_text(prefix + '.MINIMUMANNULARRINGVALUE "4.9"\n')
    selected = {}
    for kind in ("rules", "elements"):
        path = tmp_path / ("selected-" + kind)
        path.write_bytes(paths["effective_" + kind].read_bytes())
        if kind == "rules":
            path.write_text(prefix + '.MINIMUMANNULARRINGVALUE "124460"\n')
        selected[kind] = drc.file_record(path)
    with pytest.raises(ValueError, match="do not match selected settings"):
        drc.configuration_binding(selected, paths, 4)
    paths["report"].write_text(
        "DRC\n===\n检查未布线的管脚 : YES\n环形距离: 0th\nTotal DRC Hazards Found : 1\n",
        encoding="utf-8",
    )
    return selected


@pytest.mark.parametrize("late_change", [False, True])
def test_active_annular_mismatch_still_rechecks_the_output_set(tmp_path, monkeypatch, late_change):
    _, paths = reports(tmp_path)
    selected = annular_inputs(tmp_path, paths)
    parse = drc._parse_report_contents

    def refuse_then_maybe_change(*args, **kwargs):
        try:
            return parse(*args, **kwargs)
        finally:
            if late_change:
                paths["report"].write_bytes(paths["report"].read_bytes() + b"\n")

    monkeypatch.setattr(drc, "_parse_report_contents", refuse_then_maybe_change)
    with pytest.raises(drc.ReportBindingError) as caught:
        drc.read_reports(paths, {}, 1, selected=selected, engine_version="MGCPCBEngines.DRC.151")
    evidence = caught.value.observation
    assert evidence["execution_qualified"] is False
    assert evidence["annular_unit_binding"]["display_observations"][0]["raw"] == "0th"
    if late_change:
        assert "changed during report-set capture" in str(caught.value)
        assert evidence["output_set_stability"] == "failed"
        assert evidence["files"]["report"] != drc.file_record(paths["report"])
    else:
        assert evidence["files"]["report"] == drc.file_record(paths["report"])
        assert evidence["content_freshness"] == "current_invocation_files_verified"
        assert evidence["output_set_stability"] == "verified_after_parse_refusal"


@pytest.mark.parametrize("role", ["report", "violations", "effective_rules", "effective_elements"])
@pytest.mark.parametrize("fault", ["disappeared", "access_denied"])
def test_configuration_refusal_survives_final_file_failure(tmp_path, monkeypatch, role, fault):
    board, paths = reports(tmp_path)
    selected = annular_inputs(tmp_path, paths)
    parse, read = drc._parse_report_contents, drc._read_contents
    refused = False

    def refuse(*args, **kwargs):
        nonlocal refused
        try:
            return parse(*args, **kwargs)
        except drc.ReportBindingError:
            refused = True
            if fault == "disappeared":
                paths[role].unlink()
            raise

    def capture(path):
        if refused and Path(path) == paths[role] and fault == "access_denied":
            raise PermissionError("synthetic final file access denied")
        return read(path)

    monkeypatch.setattr(drc, "_parse_report_contents", refuse)
    monkeypatch.setattr(drc, "_read_contents", capture)
    with pytest.raises(drc.ReportBindingError) as caught:
        drc.read_reports(paths, {}, 1, selected=selected, layer_count=4, expected_board=board)
    evidence = caught.value.observation
    assert refused
    assert evidence["configuration_error"]["type"] == "ReportBindingError"
    assert "do not match selected settings" in evidence["configuration_error"]["message"]
    assert evidence["output_set_stability"] == "failed"
    assert evidence["output_set_stability_error"]["type"] == (
        "FileNotFoundError" if fault == "disappeared" else "PermissionError"
    )
    assert evidence["output_set_stability_error"]["role"] == role
    assert evidence["files"][role]["exists"] is True  # Actual previously consumed immutable bytes.
    assert evidence["parsed_violation_count"] == 1 and evidence["execution_qualified"] is False
    assert isinstance(caught.value.__cause__, OSError)
    assert "final report-set check failed" in str(caught.value)
    write_review_evidence(
        "final-file-" + role + "-" + fault,
        {
            "kind": "offline_file_fault_not_native_run",
            "fault": fault,
            "role": role,
            "error_type": type(caught.value).__name__,
            "message": str(caught.value),
            "observation": evidence,
        },
        tmp_path,
    )


@pytest.mark.parametrize("fault", [None, "annular", "bad_total", "timeout", "unsafe_path"])
def test_engine_boundary_preserves_one_go_and_failed_report_evidence(tmp_path, monkeypatch, fault):
    from xpedition_cli import cell_refresh, native_identity
    from xpedition_cli.native_com_adapter import AdapterError

    board, paths = reports(tmp_path)
    calls = []
    selected = {}
    for kind in ("rules", "elements"):
        path = tmp_path / ("input-" + kind)
        path.write_bytes(paths["effective_" + kind].read_bytes())
        if kind == "rules" and fault == "annular":
            path.write_text(
                '.FILETYPE BATCH_DRC_FILE\n.VERSION "2.02"\n.BASE_UNITS 0\n'
                '.MINIMUMANNULARRING YES\n.MINIMUMANNULARRINGVALUE "124460"\n'
            )
        selected[kind] = str(path)
    monkeypatch.setattr(cell_refresh, "capture", lambda *a: {"model": {"layers": 4}})
    monkeypatch.setattr(
        native_identity, "application_identity", lambda *a: {"product_version": "fake151"}
    )
    monkeypatch.setattr(drc, "registered_engine", lambda: "MGCPCBEngines.DRC.151")

    def save_close(*args, lifecycle):
        calls.append("save_close")
        lifecycle.update(save_count=1, close_count=1, save_acknowledged=True, closed_observed=True)

    class Engine:
        Errors = []

        def Go(self):
            calls.append("go")
            if fault == "timeout":
                raise TimeoutError("synthetic timeout")
            for path in paths.values():
                path.write_bytes(path.read_bytes())
            for kind in selected:
                paths["effective_" + kind].write_bytes(Path(selected[kind]).read_bytes())
            if fault == "annular":
                paths["effective_rules"].write_bytes(
                    paths["effective_rules"].read_bytes().replace(b"124460", b"4.9")
                )
                paths["report"].write_text(
                    "DRC\n===\n环形距离: 0th\nTotal DRC Hazards Found : 1\n", encoding="utf-8"
                )
            if fault == "bad_total":
                paths["report"].write_text("DRC\n===\nTotal DRC Hazards Found : 0\n")

    if fault == "unsafe_path":
        monkeypatch.setattr(
            drc,
            "validate_output_paths",
            lambda *a: (_ for _ in ()).throw(ValueError("unsafe output")),
        )
    native = SimpleNamespace(
        AdapterError=AdapterError,
        _layout_board_path=lambda params: board,
        _application=lambda *a, **k: object(),
        _licensed_document=lambda app: SimpleNamespace(FullName=str(board)),
        _save_close_board=save_close,
        _native_call=lambda label, function: function(),
    )
    client = SimpleNamespace(Dispatch=lambda progid: Engine())
    if fault:
        with pytest.raises(AdapterError) as caught:
            drc.run({"project": str(tmp_path / "owned.prj"), **selected}, client, native)
        if fault == "unsafe_path":
            assert caught.value.details["write_attempted"] is False and calls == []
        else:
            result = caught.value.details["native_result"]
            assert result["source"]["complete"] is False and not result["passes"]
            assert calls == ["save_close", "go"]
            if fault == "annular":
                observation = result["unqualified_report_observation"]
                assert observation["execution_qualified"] is False
                assert (
                    observation["annular_unit_binding"]["display_observations"][0]["raw"] == "0th"
                )
            if fault == "timeout":
                assert caught.value.code == "E_TIMEOUT" and result["completion"]["timed_out"]
    else:
        result = drc.run({"project": str(tmp_path / "owned.prj"), **selected}, client, native)
        assert calls == ["save_close", "go"]
        assert result["completed"] and result["count"] == 1 and not result["passes"]
        assert result["native_reports"]["configuration_binding"]["verified"]
