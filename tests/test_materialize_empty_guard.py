"""Materialization's actual planner -> HKP -> import chain, without CAD."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import library_hkp as H
from xpedition_cli import native_com_adapter as A


def _adapter_fixture(tmp_path, monkeypatch):
    project = tmp_path / "Demo.prj"
    project.write_text("", encoding="utf-8")
    root = tmp_path / "Library"
    root.mkdir()
    lmc = root / "Library.lmc"
    lmc.write_text("", encoding="utf-8")
    calls = {key: 0 for key in ("converter", "save", "close", "open", "registration")}
    app = SimpleNamespace(
        IsProjectOpened=lambda: True,
        GetProjectData=lambda: SimpleNamespace(GetProjectFilePath=lambda: str(project)),
        Documents=SimpleNamespace(SaveAll=lambda: calls.__setitem__("save", calls["save"] + 1)),
    )
    opened = True

    def close():
        nonlocal opened
        calls["close"] += 1
        opened = False
        return True

    app.CloseProject = close
    app.IsProjectOpened = lambda: opened

    def reopen(*args):
        calls["open"] += 1

    def convert(tool, arguments):
        calls["converter"] += 1
        return {"exit_code": 0, "dialogs": [], "tool": tool}

    def register(*args):
        calls["registration"] += 1
        return {"status": "fixture"}

    from xpedition_cli import native_library_manager

    monkeypatch.setattr(A, "_central_library", lambda path: (lmc, root))
    monkeypatch.setattr(A, "_viewdraw_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(A, "_ensure_project", reopen)
    monkeypatch.setattr(A, "_run_library_tool", convert)
    monkeypatch.setattr(A, "_tool_log", lambda path: {"errors": [], "warnings": []})
    monkeypatch.setattr(A, "_sync_prj_cells", lambda *args: [])
    monkeypatch.setattr(A, "_ensure_prj_pdb", lambda *args: True)
    monkeypatch.setattr(A, "_settle", lambda seconds: None)
    monkeypatch.setattr(native_library_manager, "require_partitions", register)
    return project, root, calls


@pytest.mark.parametrize("shape", ["generated_headers", "empty", "empty_units"])
def test_import_rejects_no_objects_before_converter_or_project_lifecycle(
    tmp_path, monkeypatch, shape
):
    project, root, calls = _adapter_fixture(tmp_path, monkeypatch)
    plan = H.LibraryPlan()
    texts = {
        "padstacks": H.render_padstacks(plan),
        "cells": H.render_cells(plan),
        "parts": H.render_parts(plan),
    }
    assert all(texts.values())  # The real bug: truthy headers, zero native objects.
    if shape == "empty":
        texts = {key: "" for key in texts}
    params = {"project": str(project), "partition": "PartQuest", **texts}
    if shape == "empty_units":
        params = {"project": str(project), "units": []}
    error = None
    try:
        A._library_import(params, None)
    except A.AdapterError as exc:
        error = exc
    assert error is not None, (shape, calls)
    assert error.code == "E_VALIDATION"
    assert error.details["write_attempted"] is False
    assert error.details["materialization_not_needed"] is True
    assert calls == dict.fromkeys(calls, 0)
    assert not (root / "Work").exists()


def _bound_design():
    return {
        "sheet_size": "A4",
        "symbols": {"R": {"part": "RES-10K"}},
        "sheets": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "ic",
                        "refdes": f"R{index + 1}",
                        "symbol": "R",
                        "x": 200 + (index % 4) * 200,
                        "y": 300 + (index // 4) * 300,
                        "pins": {"1": f"label:IN{index + 1}", "2": "gnd"},
                    }
                    for index in range(8)
                ],
            }
        ],
    }


@pytest.fixture
def bound(cli, adapter, project, tmp_path):
    from fakes import FakeLibrary

    library = FakeLibrary(tmp_path / "Lib")
    adapter.on("library_export", library.export)
    adapter.on("library_import", library.absorb)
    path = tmp_path / "bound.json"
    path.write_text(json.dumps(_bound_design()), encoding="utf-8")
    return ["library", "materialize", "--project", str(project), "--design", str(path)], path


def test_all_bound_preview_is_ineligible_and_preserves_eight_mappings(cli, adapter, bound):
    args, _ = bound
    code, payload = cli(*args, "--dry-run")
    assert code == 0, payload
    preview = payload["data"]["preview"]
    assert preview["materialization_not_needed"] is True
    assert preview["can_apply"] is False
    assert preview["changes"] == []
    assert preview["parts"] == preview["cells"] == preview["padstacks"] == 0
    assert len(preview["summary"]["mapping"]) == 8
    assert all(item["library_part"] is True for item in preview["summary"]["mapping"])
    assert preview["next_action"] == "run schematic package as a separate confirmed operation"
    code, refused = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 6, refused
    assert refused["error"]["details"]["write_attempted"] is False
    assert "library_import" not in adapter.methods()


def test_legacy_eligible_token_cannot_dispatch_an_all_bound_plan(cli, adapter, bound):
    from xpedition_cli.cli.library import design_parts, load_library
    from xpedition_cli.confirm import issue

    args, path = bound
    # Exact pre-fix token scope: a real eligible token does not bypass re-planning.
    design = json.loads(path.read_text(encoding="utf-8"))
    held, _ = load_library({}, Path(args[3]), kinds=("parts",))
    parts = design_parts(held, design, "library materialize")
    plan, texts = H.library_texts(design, None, parts)
    import hashlib

    digest = hashlib.sha256("".join(texts[key] for key in sorted(texts)).encode()).hexdigest()
    token, _ = issue(
        {
            "operation": "library_build",
            "project": str(Path(args[3]).resolve()),
            "partition": plan.partition,
            "digest": digest[:16],
            "package": False,
        }
    )
    code, payload = cli(*args, "--confirm", token)
    assert code == 6, payload
    assert payload["error"]["details"]["write_attempted"] is False
    assert "library_import" not in adapter.methods()


def _add_placeholder(design):
    design["sheets"][0]["blocks"].append(
        {
            "kind": "ladder",
            "x": 700,
            "y": 950,
            "path": [
                "label:EXTRA_IN",
                {"refdes": "R9", "symbol": "RES", "value": "47k"},
                "label:EXTRA_OUT",
            ],
        }
    )
    return design


def test_nonempty_mixed_plan_still_limits_generated_objects_to_the_placeholder(cli, adapter, bound):
    from xpedition_cli import library_read as R

    args, path = bound
    path.write_text(json.dumps(_add_placeholder(_bound_design())), encoding="utf-8")
    code, planned = cli(*args, "--dry-run")
    preview = planned["data"]["preview"]
    assert code == 0 and preview["can_apply"] is True
    assert preview["materialization_not_needed"] is False
    assert len(preview["summary"]["mapping"]) == 9
    assert preview["summary"]["parts"] == ["47k"]
    code, applied = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 0, applied
    sent = adapter.last("library_import")
    assert [part["number"] for part in R.parse_parts(sent["parts"])] == ["47k"]
    # This only preserves the request boundary; fake imports prove no native merge.
    assert "RES-10K" not in [part["number"] for part in R.parse_parts(sent["parts"])]


@pytest.mark.parametrize("initially_mixed", [True, False])
def test_input_drift_never_dispatches_an_import(cli, adapter, bound, initially_mixed):
    args, path = bound
    before = _add_placeholder(_bound_design()) if initially_mixed else _bound_design()
    after = _bound_design() if initially_mixed else _add_placeholder(_bound_design())
    path.write_text(json.dumps(before), encoding="utf-8")
    _, planned = cli(*args, "--dry-run")
    path.write_text(json.dumps(after), encoding="utf-8")
    code, refused = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 6, refused
    assert "library_import" not in adapter.methods()


def test_header_only_components_of_a_nonempty_import_never_run_their_converter(
    tmp_path, monkeypatch
):
    project, _, calls = _adapter_fixture(tmp_path, monkeypatch)
    headers = H.LibraryPlan()
    result = A._library_import(
        {
            "project": str(project),
            "partition": "PartQuest",
            "parts": '.Filetype ASCII_PDB\n.Number "R1"\n..TopCell "R"\n',
            "cells": H.render_cells(headers),
            "padstacks": H.render_padstacks(headers),
        },
        None,
    )
    assert result["ok"] is True
    assert [step["tool"] for step in result["steps"]] == ["HKP2PartsDB"]
    assert calls == {"converter": 1, "save": 1, "close": 1, "open": 1, "registration": 1}


def test_comments_and_header_variations_are_not_native_objects(tmp_path, monkeypatch):
    project, _, calls = _adapter_fixture(tmp_path, monkeypatch)
    with pytest.raises(A.AdapterError) as caught:
        A._library_import(
            {
                "project": str(project),
                "units": [
                    {
                        "partition": "PartQuest",
                        "parts": ' ! example\n.Filetype ASCII_PDB\n.Version "02.02"\n',
                        "cells": ".FILETYPE CELL_LIBRARY\n.UNITS MM\n! No cells\n",
                    }
                ],
                "padstacks": "! No padstack records\n",
            },
            None,
        )
    assert caught.value.details["write_attempted"] is False
    assert calls == dict.fromkeys(calls, 0)


def test_reference_describes_the_public_empty_plan_boundary(cli):
    code, payload = cli("reference", "--schema", "library_build_preview")
    assert code == 0, payload
    schema = payload["data"]["schemas"]["library_build_preview"]
    assert "materialization_not_needed" in schema["materialization_preview_shape"]
    assert "Save/Close/Open" in schema["empty_plan_contract"]
    assert "earlier unknown" in schema["empty_plan_contract"]
