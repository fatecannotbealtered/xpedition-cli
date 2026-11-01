"""Adversarial closed-state read limits and terminal gating; no native APIs."""

import copy
from pathlib import Path

import pytest
from drc_fakes import application_identity, process_identity
from test_driver_v2 import _worker_fixture

from xpedition_cli import drc_engine as drc
from xpedition_cli import drc_failure_observation as observer


def scenario(tmp_path):
    board, project = tmp_path / "PCB/board.pcb", tmp_path / "board.prj"
    board.parent.mkdir()
    board.write_bytes(b"synthetic board")
    project.write_bytes(b"synthetic project")
    for row in drc.planned_side_effects(board):
        if row["role"] == "staged_inputs":
            continue
        path = Path(row["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic output")
    worker = _worker_fixture()
    bound = observer.binding(
        project,
        board,
        application_identity(board),
        worker,
        worker,
        process_identity(tmp_path),
        drc.planned_side_effects(board),
    )
    bound["geometry_before_sha256"] = "c" * 64
    result = {
        "pcb": str(board),
        "source": {"input": {"requested_project": str(project), "snapshot_sha256": "c" * 64}},
        "native_stages": {"go": "returned", "report_read": "failed"},
        "native_go_dispatched": True,
        "native_go_returned": True,
        "board_lifecycle": {"closed_observed": True},
    }
    identities = []

    def identity():
        identities.append("identity")
        return {"application": application_identity(board, opened=False), "adapter": worker}

    def observe():
        return observer.observe(
            bound,
            result,
            identity_reader=identity,
            process_reader=lambda pid: process_identity(tmp_path),
            controller_reader=lambda: worker,
        )

    return bound, result, observe, identities


@pytest.mark.parametrize("fault", ["timeout", "partial_fields"])
def test_unconfirmed_native_terminal_does_not_call_com_identity_or_open(tmp_path, fault):
    bound, result, observe, identities = scenario(tmp_path)
    result["native_go_returned"] = False
    result["native_stages"]["go"] = "timed_out"
    if fault == "partial_fields":
        del result["native_go_dispatched"]
        result["native_stages"]["go"] = "started"
    evidence = observe()
    assert identities == []
    assert not evidence["complete"] and not evidence["original_call_terminal_observed"]
    assert not evidence["reopen"]["attempted"] and not evidence["reopen"]["authorized"]
    assert len(evidence["second"]["files"]) == 12
    assert all(
        v["reason"] == "original_native_call_terminal_unconfirmed" for v in evidence["issues"]
    )


@pytest.mark.parametrize("limit", ["files", "bytes"])
def test_read_budget_remains_bounded_across_failed_roles_and_both_frames(
    tmp_path, monkeypatch, limit
):
    _, _, observe, _ = scenario(tmp_path)
    reads, reserve_sizes = [], []
    read = drc._read_contents

    def tracked(path, *, reserve=None):
        reads.append(str(path))

        def charge(size):
            reserve_sizes.append(size)
            return reserve(size)

        return read(path, reserve=charge)

    monkeypatch.setattr(drc, "_read_contents", tracked)
    monkeypatch.setattr(
        observer, "MAX_FILES" if limit == "files" else "MAX_BYTES", 2 if limit == "files" else 1
    )
    evidence = observe()
    assert not evidence["complete"]
    assert len(evidence["second"]["files"]) == 12
    assert all(
        evidence[frame]["files"]["board_log"].get("read_error") for frame in ["first", "second"]
    )
    assert len(reads) == (4 if limit == "files" else 2)
    assert reserve_sizes  # Reservation is before the attempted stream read, including refusal.


@pytest.mark.parametrize("drift", ["board", "side_effect", "candidate"])
def test_foreign_binding_is_rejected_before_any_file_capture(tmp_path, monkeypatch, drift):
    bound, result, observe, identities = scenario(tmp_path)
    if drift == "board":
        result["pcb"] = str(tmp_path / "foreign.pcb")
    elif drift == "side_effect":
        bound["side_effects"][0]["path"] = str(tmp_path / "foreign-report.txt")
    else:
        bound["controller"] = copy.deepcopy(bound["controller"])
        bound["controller"]["build_sha256"] = "b" * 64
    monkeypatch.setattr(observer, "_capture", lambda *a: pytest.fail("Foreign bound file read"))
    with pytest.raises(ValueError, match="confirmed target"):
        observe()
    assert not identities


def test_partial_staging_failure_retains_already_consumed_file_records(tmp_path, monkeypatch):
    bound, _, observe, _ = scenario(tmp_path)
    staged = Path(
        next(row["path"] for row in bound["side_effects"] if row["role"] == "staged_inputs")
    )
    staged.mkdir(parents=True)
    (staged / "first.drc").write_bytes(b"synthetic first")
    (staged / "second.e2e").write_bytes(b"synthetic second")
    read = drc._read_contents
    count = 0

    def captured(path, **kwargs):
        nonlocal count
        if Path(path).parent == staged:
            count += 1
            if count % 2 == 0:
                raise PermissionError("synthetic second staging file access failure")
        return read(path, **kwargs)

    monkeypatch.setattr(drc, "_read_contents", captured)
    evidence = observe()
    assert not evidence["complete"]
    for frame in ("first", "second"):
        row = evidence[frame]["files"]["staged_inputs"]
        assert row["read_error"]["type"] == "PermissionError"
        assert len(row["files"]) == 1 and row["files"][0]["sha256"]
