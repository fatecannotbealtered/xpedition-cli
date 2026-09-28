"""library build (library import is covered in test_kicad_import_command)."""

from __future__ import annotations

import json
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-sensor-board.json"


def _design(tmp_path: Path) -> Path:
    path = tmp_path / "design.json"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _imported(params: dict) -> dict:
    return {
        "project": params["project"],
        "library": "C:/Lib/Lib.lmc",
        "partition": params["partition"],
        "steps": [],
        "failed": [],
        "pdb_registered": True,
        "cells_registered": [],
        "cells_missing": [],
        "ok": True,
        "hint": None,
    }


def test_the_dry_run_plans_every_part_without_xpedition(cli, adapter, project, tmp_path) -> None:
    design = _design(tmp_path)
    code, payload = cli(
        "library", "build", "--project", str(project), "--design", str(design), "--dry-run"
    )
    preview = payload["data"]["preview"]
    assert code == 0 and preview["partition"] == "PartQuest"
    assert preview["parts"] > 0 and preview["cells"] > 0 and preview["padstacks"] > 0
    assert preview["summary"]["parts"]
    assert "package_design" not in [change["action"] for change in preview["changes"]]
    assert adapter.calls == []


def test_the_confirmed_run_imports_and_with_package_packages(
    cli, adapter, project, tmp_path
) -> None:
    design = _design(tmp_path)
    adapter.on("library_import", _imported)
    adapter.on("package", {"packaged": True, "errors": []})
    args = ["library", "build", "--project", str(project), "--design", str(design), "--package"]
    _, payload = cli(*args, "--dry-run")
    assert payload["data"]["preview"]["changes"][-1] == {"action": "package_design"}
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["ok"] is True
    assert payload["data"]["package"] == {"packaged": True, "errors": []}
    assert adapter.methods() == ["library_import", "package"]
    sent = adapter.last("library_import")
    assert sent["partition"] == "PartQuest" and {"padstacks", "cells", "parts"} <= set(sent)


def test_a_failed_import_is_not_packaged(cli, adapter, project, tmp_path) -> None:
    design = _design(tmp_path)
    adapter.on(
        "library_import", lambda params: {**_imported(params), "ok": False, "failed": ["cells"]}
    )
    args = ["library", "build", "--project", str(project), "--design", str(design), "--package"]
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 0 and payload["data"]["ok"] is False and "package" not in payload["data"]
    assert adapter.methods() == ["library_import"]


def test_the_token_binds_the_package_choice_and_the_partition(
    cli, adapter, project, tmp_path
) -> None:
    design = _design(tmp_path)
    args = ["library", "build", "--project", str(project), "--design", str(design)]
    _, payload = cli(*args, "--dry-run")
    token = payload["data"]["confirm_token"]
    code, payload = cli(*args, "--package", "--confirm", token)
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    _, payload = cli(*args, "--dry-run")
    code, payload = cli(
        *args, "--partition", "Other", "--confirm", payload["data"]["confirm_token"]
    )
    assert code == 6
    assert adapter.calls == []


def test_a_design_that_cannot_be_packaged_is_refused(cli, adapter, project, tmp_path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"sheets": "nope"}), encoding="utf-8")
    code, payload = cli(
        "library", "build", "--project", str(project), "--design", str(broken), "--dry-run"
    )
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
    code, payload = cli(
        "library",
        "build",
        "--project",
        str(project),
        "--design",
        str(tmp_path / "none.json"),
        "--dry-run",
    )
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    not_json = tmp_path / "bad.json"
    not_json.write_text("{", encoding="utf-8")
    code, payload = cli(
        "library", "build", "--project", str(project), "--design", str(not_json), "--dry-run"
    )
    assert code == 2


def test_build_without_the_adapter_says_so_after_the_dry_run(
    cli, project, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", "Z:/no/such/adapter.exe")
    design = _design(tmp_path)
    args = ["library", "build", "--project", str(project), "--design", str(design)]
    code, payload = cli(*args, "--dry-run")
    assert code == 0
    code, payload = cli(*args, "--confirm", payload["data"]["confirm_token"])
    assert code == 4 and payload["error"]["code"] == "E_BACKEND_UNAVAILABLE"
    assert payload["error"]["details"]["hint"]
