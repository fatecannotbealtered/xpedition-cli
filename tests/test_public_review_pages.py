"""Public SDK read-query pages with real handlers and isolated source facts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xpedition_cli.cli import bom, library
from xpedition_cli.driver import runtime


@pytest.mark.parametrize("command", ["library check", "bom inspect"])
@pytest.mark.parametrize("offset", [0, 2, 4])
def test_specialized_read_pages_preserve_items_next_page_and_extensions(
    tmp_path, monkeypatch, command, offset
):
    expected_rows = [
        {
            "id": str(i),
            "severity": "high",
            "rule": "fixture",
            "item": str(i),
            "message": "fixture finding",
        }
        for i in range(5)
    ]
    if command == "library check":
        path = tmp_path / "Library.lmc"
        path.write_bytes(b"fixture")
        held = SimpleNamespace(parts=[{"partition": "QA"}], cells=[1, 2, 3], symbols=[1, 2])
        metadata = {
            "library": str(path),
            "failed": [],
            "registration": {"status": "not_observed", "source": "fixture"},
        }
        monkeypatch.setattr(library, "load_library", lambda *a, **kw: (held, metadata))
        from xpedition_cli import library_read

        monkeypatch.setattr(library_read, "check", lambda *a: expected_rows)
        args = ["library", "check", "--library", str(path)]
        expected_extensions = {
            "clean": False,
            "registration": metadata["registration"],
            "counts": {"high": 5, "medium": 0, "low": 0},
            "parts": 1,
            "cells": 3,
            "symbols": 2,
        }
    else:
        path = tmp_path / "QA.prj"
        path.write_bytes(b"fixture")
        expected_rows = [
            {
                "refdes": f"R{i}",
                "internal_part_no": f"PART-{i}",
                "quantity": 1,
                "value": "10K",
                "package": "0805",
            }
            for i in range(5)
        ]
        monkeypatch.setattr(bom, "_rows", lambda *a: ({"project": str(path)}, expected_rows))
        args = ["bom", "inspect", "--project", str(path)]
        expected_extensions = {"parts": 5, "grouped": False}
    with runtime(state_dir=tmp_path / "state") as cli:
        output = cli.run([*args, "--limit", "2", "--offset", str(offset)])
    assert output.exit_code == 0, output.envelope
    page = output.envelope["data"]
    assert page["items"] == expected_rows[offset : offset + 2]
    assert page["count"] == len(expected_rows[offset : offset + 2])
    assert page["offset"] == offset
    assert page["has_more"] is (offset < 4)
    if offset < 4:
        assert page["next_offset"] == offset + 2
    else:
        assert "next_offset" not in page
    assert "next_cursor" not in page  # These handlers implement offset pagination.
    for field, expected in expected_extensions.items():
        assert page["extensions"][field] == expected


def test_bom_check_is_an_aggregate_validation_not_an_inspection_page(tmp_path, monkeypatch):
    path = tmp_path / "QA.prj"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        bom,
        "_rows",
        lambda *a: ({"project": str(path)}, [{"refdes": "R1", "internal_part_no": None}]),
    )
    with runtime(state_dir=tmp_path / "state") as cli:
        output = cli.run(["bom", "check", "--project", str(path)])
    assert output.exit_code == 0, output.envelope
    data = output.envelope["data"]
    assert data["valid"] is False and data["parts"] == 1 and data["part_numbers"] == 0
    assert data["issues"][0]["kind"] == "missing_part_number"
    assert "items" not in data and "next_offset" not in data


def test_native_state_conflict_retains_canonical_error_at_public_boundary(tmp_path, monkeypatch):
    from xpedition_cli import driver
    from xpedition_cli.errors import CLIError

    def conflict(options):
        raise CLIError("E_CONFLICT", "Native project changed", {"observed_project": None})

    monkeypatch.setattr(driver, "_handler", lambda declaration: conflict)
    path = tmp_path / "QA.lmc"
    path.write_bytes(b"fixture")
    with runtime(state_dir=tmp_path / "state") as cli:
        output = cli.run(["library", "check", "--library", str(path)])
    assert output.exit_code == 6, output.envelope
    error = output.envelope["error"]
    assert error["code"] == "E_CONFLICT"
    assert error["details"]["conflict"] == "state_drift"
    assert error["details"]["observed_project"] is None
