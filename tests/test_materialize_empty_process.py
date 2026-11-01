"""Real public CLI processes with synthetic library reads, never CAD acceptance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_main():
    import pytest
    from fakes import FakeLibrary
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli import driver
    from xpedition_cli.cli import library
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_MATERIALIZE_PROCESS_FIXTURE"])
    patches = pytest.MonkeyPatch()
    _scope(folder, patches)
    library_folder = folder / "owned" / "Lib"
    if not (library_folder / "Lib.lmc").exists():
        held = FakeLibrary(library_folder)
    else:
        # A second CLI process observes the exact frozen source, without merging
        # the fixture's own texts again and accidentally manufacturing drift.
        held = FakeLibrary.__new__(FakeLibrary)
        held.folder = library_folder
        held.cache = library_folder / "cache"
        held.symbols = library_folder / "SymbolLibs"
        held.lmc = library_folder / "Lib.lmc"
        held.texts = {}
    calls = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            calls.append(method)
            if method == "identity":
                return {
                    "application": {"pid": 4242, "domain": "schematic"},
                    "adapter": _worker_fixture(),
                }
            if method == "library_export":
                return held.export(params)
            raise AssertionError("No native write permitted in this process fixture: " + method)

    backend = Backend()
    patches.setattr(driver, "NativeBackend", lambda: backend)
    patches.setattr(library, "reader", lambda options: backend)
    patches.setattr(library, "native", lambda: backend)
    if os.environ.get("XPEDITION_MATERIALIZE_PROCESS_LEGACY") == "1":
        original = library.build

        def legacy_eligible_preview(options):
            result = original(options)
            if options.get("dry_run"):
                result["preview"]["can_apply"] = True
            return result

        # Only the old eligible plan flag is reconstructed. Confirmation uses
        # the unmodified current handler in a separate process below.
        patches.setattr(library, "build", legacy_eligible_preview)
    try:
        return main()
    finally:
        (folder / "process-calls.json").write_text(json.dumps(calls), encoding="utf-8")
        patches.undo()


def _run(folder, args, *, legacy=False):
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(ROOT / 'tests')!r}); "
        "from test_materialize_empty_process import _synthetic_main; "
        "raise SystemExit(_synthetic_main())"
    )
    result = subprocess.run(
        [sys.executable, "-c", bootstrap, *args, "--compact"],
        cwd=ROOT,
        env={
            **os.environ,
            "XPEDITION_CLI_CONFIG_DIR": str(folder / "config"),
            "XPEDITION_NATIVE_COMMAND": str(folder / "no-live-adapter.exe"),
            "XPEDITION_MATERIALIZE_PROCESS_FIXTURE": str(folder),
            "XPEDITION_MATERIALIZE_PROCESS_LEGACY": "1" if legacy else "0",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    body = json.loads(result.stdout)
    calls = json.loads((folder / "process-calls.json").read_text(encoding="utf-8"))
    assert set(calls) <= {"identity", "library_export"}
    return result, body, calls


@pytest.mark.parametrize("legacy", [False, True])
def test_public_process_exposes_eight_real_mappings_and_refuses_empty_import(tmp_path, legacy):
    from test_materialize_empty_guard import _bound_design

    (tmp_path / "owned").mkdir()
    project = tmp_path / "owned" / "Board.prj"
    project.write_text('KEY CentralLibrary "Lib/Lib.lmc"\n', encoding="utf-8")
    design = tmp_path / "bound.json"
    design.write_text(json.dumps(_bound_design()), encoding="utf-8")
    args = ["library", "materialize", "--project", str(project), "--design", str(design)]
    result, body, _ = _run(tmp_path, [*args, "--dry-run"], legacy=legacy)
    assert result.returncode == 0, (body, result.stderr)
    plan = body["data"]["preview"]
    preview = plan["extensions"]["legacy_preview"]
    assert plan["can_apply"] is legacy and plan["changes"] == []
    assert preview["materialization_not_needed"] is True
    assert preview["changes"] == [] and preview["parts"] == 0
    assert len(preview["summary"]["mapping"]) == 8
    assert all(row["library_part"] is True for row in preview["summary"]["mapping"])
    assert "schematic package" in preview["next_action"]
    token = body["data"]["confirm_token"]
    result, refused, calls = _run(tmp_path, [*args, "--confirm", token])
    assert result.returncode != 0 and refused["ok"] is False, refused
    assert "library_import" not in calls
    if legacy:
        assert refused["error"]["details"]["materialization_not_needed"] is True, refused
        assert refused["error"]["details"]["write_attempted"] is False
    # Repeating confirmation is also incapable of creating a converter/project write.
    result, refused, calls = _run(tmp_path, [*args, "--confirm", token])
    assert result.returncode != 0 and refused["ok"] is False, refused
    assert "library_import" not in calls
