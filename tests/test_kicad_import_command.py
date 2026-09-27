"""`library kicad-import`: a gated write whose dry run reads only files."""

from __future__ import annotations

import json
import subprocess

import pytest

from xpedition_cli import main as cli
from xpedition_cli.backends import native_xpedition


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "footprints"
    for library, names in (("Package_SO", ["SOIC-8", "SOIC-14"]), ("Resistor_SMD", ["R_0603"])):
        folder = root / f"{library}.pretty"
        folder.mkdir(parents=True)
        for name in names:
            (folder / f"{name}.kicad_mod").write_text("(footprint)", encoding="utf-8")
    library_root = tmp_path / "Lib"
    (library_root / "CellDBLibs").mkdir(parents=True)
    (library_root / "Central.lmc").write_bytes(b"lmc")
    project = tmp_path / "demo.prj"
    project.write_text(
        'SECTION DesignInfo\nKEY CentralLibrary "Lib\\Central.lmc"\nENDSECTION\n'.replace(
            "\\", "/"
        ),
        encoding="utf-8",
    )
    args = ["library", "kicad-import", "--project", str(project), "--root", str(root)]
    return {"args": args, "cells": library_root / "CellDBLibs"}


def test_the_dry_run_lists_what_would_be_converted(capsys, setup) -> None:
    (setup["cells"] / "Package_SO.cel").write_bytes(b"cel")
    code, result = run(capsys, *setup["args"], "--dry-run")
    assert code == 0, result
    preview = result["data"]["preview"]
    assert [row["library"] for row in preview["libraries"]] == ["Package_SO", "Resistor_SMD"]
    assert preview["footprints"] == 3
    assert [row["exists"] for row in preview["libraries"]] == [True, False]
    actions = [change["action"] for change in preview["changes"]]
    assert actions == ["close_designer_project", "merge_partition", "create_partition"]
    assert result["data"]["confirm_token"].startswith("ct_")


def test_libraries_narrow_the_import_and_repeat_like_any_plural_flag(capsys, setup) -> None:
    args = [*setup["args"], "--libraries", "resistor_smd", "--libraries", "Resistor_SMD.pretty"]
    _, result = run(capsys, *args, "--dry-run")
    assert [row["library"] for row in result["data"]["preview"]["libraries"]] == ["Resistor_SMD"]


@pytest.mark.parametrize(
    ("extra", "code", "error"),
    [
        (["--libraries", "Nope"], 3, "E_NOT_FOUND"),
        (["--libraries", ","], 2, "E_VALIDATION"),
        ([], 5, "E_CONFIRMATION_REQUIRED"),
    ],
)
def test_mistakes_are_refused_before_anything_runs(capsys, setup, extra, code, error) -> None:
    got, result = run(capsys, *setup["args"], *extra, *(["--dry-run"] if extra else []))
    assert got == code and result["error"]["code"] == error


def test_a_project_is_required(capsys) -> None:
    code, result = run(capsys, "library", "kicad-import", "--dry-run")
    assert code == 2 and result["error"]["code"] == "E_USAGE"


@pytest.fixture
def native(tmp_path, monkeypatch):
    binary = tmp_path / "native-adapter.exe"
    binary.write_bytes(b"placeholder")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    monkeypatch.setattr(native_xpedition.NativeBackend, "require_implemented", lambda self: None)
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        request = json.loads(kwargs["input"])
        calls.append(request)
        partitions = [
            {"library": name, "partition": name, "cells": 2, "padstacks": 3, "issues": 0}
            | {"ok": name != "Resistor_SMD", "steps": []}
            for name in request["params"]["libraries"]
        ]
        data = {"partitions": partitions, "failed": ["Resistor_SMD"], "ok": False}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")

    monkeypatch.setattr(native_xpedition.subprocess, "run", fake_run)
    return calls


def test_the_confirmed_run_reports_each_library(capsys, setup, native) -> None:
    args = [*setup["args"], "--backend", "native_xpedition"]
    _, dry = run(capsys, *args, "--dry-run")
    code, result = run(capsys, *args, "--confirm", dry["data"]["confirm_token"])
    assert code == 0, result
    [request] = native
    assert request["method"] == "kicad_import"
    assert request["params"]["libraries"] == ["Package_SO", "Resistor_SMD"]
    items = result["data"]["items"]
    assert [(item["target"], item["ok"]) for item in items] == [
        ("Package_SO", True),
        ("Resistor_SMD", False),
    ]
    assert items[1]["error"]["code"] == "E_IO"
    assert result["data"]["summary"] == {"total": 2, "succeeded": 1, "failed": 1}


def test_a_partition_imported_since_the_dry_run_refuses_the_token(capsys, setup, native) -> None:
    args = [*setup["args"], "--backend", "native_xpedition"]
    _, dry = run(capsys, *args, "--dry-run")
    (setup["cells"] / "Resistor_SMD.cel").write_bytes(b"cel")
    token = dry["data"]["confirm_token"]
    code, result = run(capsys, *args, "--dangerous", "--confirm", token)
    assert code == 6 and result["error"]["code"] == "E_CONFLICT"
    assert native == []


def test_merging_into_a_partition_that_exists_needs_dangerous(capsys, setup, native) -> None:
    (setup["cells"] / "Package_SO.cel").write_bytes(b"cel")
    args = [*setup["args"], "--backend", "native_xpedition"]
    _, dry = run(capsys, *args, "--dry-run")
    assert dry["data"]["preview"]["dangerous"] is True
    token = dry["data"]["confirm_token"]
    code, refused = run(capsys, *args, "--confirm", token)
    assert code == 5 and refused["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert native == []
    code, result = run(capsys, *args, "--dangerous", "--confirm", token)
    assert code == 0, result


def test_stopping_at_the_first_failure_is_passed_on(capsys, setup, native) -> None:
    args = [*setup["args"], "--backend", "native_xpedition", "--continue-on-error", "false"]
    _, dry = run(capsys, *args, "--dry-run")
    code, _ = run(capsys, *args, "--confirm", dry["data"]["confirm_token"])
    assert code == 0
    assert native[-1]["params"]["continue_on_error"] is False
    code, result = run(capsys, *setup["args"], "--continue-on-error", "maybe", "--dry-run")
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"


def test_the_token_binds_the_batch_policy(capsys, setup, native) -> None:
    args = [*setup["args"], "--backend", "native_xpedition"]
    _, dry = run(capsys, *args, "--dry-run")
    token = dry["data"]["confirm_token"]
    code, result = run(capsys, *args, "--continue-on-error", "false", "--confirm", token)
    assert code == 6 and result["error"]["code"] == "E_CONFLICT"
    assert native == []
