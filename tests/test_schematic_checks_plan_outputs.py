"""Public check plans retain native report paths under their declared effects."""

from __future__ import annotations

import copy
import json

import pytest
from fakes import schematic_snapshot
from test_driver_v2 import _scope
from test_schematic_edit_protection import _snapshot as complete_edit_snapshot

from xpedition_cli import __version__, driver
from xpedition_cli.audit import config_dir
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.schematic_attribute_display import freeze_save_metadata


def _public_plan(tmp_path, monkeypatch, command=("schematic", "checks", "run"), fake_handler=False):
    _scope(tmp_path, monkeypatch)
    scope_path = tmp_path / "scope.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    scope["outputs"].append(str(config_dir().resolve()))
    scope_path.write_text(json.dumps(scope), encoding="utf-8")
    project = tmp_path / "Board1.prj"
    project.write_text("offline checker fixture", encoding="utf-8")
    calls = []
    snapshot = (
        complete_edit_snapshot() if command == ("schematic", "edit") else schematic_snapshot()
    )

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            calls.append(method)
            if method == "identity":
                return {
                    "application": {
                        "pid": 4242,
                        "document": {"open": True, "path": str(project)},
                        "fixture": True,
                    },
                    "adapter": {"version": __version__, "build_sha256": "a" * 64, "fixture": True},
                }
            assert method == "snapshot", "a dry-run must not execute a native checker"
            assert params["observe_only"] is True
            return copy.deepcopy(snapshot)

    args = [*command, "--project", str(project), "--dry-run"]
    if command == ("schematic", "edit"):
        changes = tmp_path / "changes.json"
        changes.write_text(
            json.dumps(
                {"operations": [{"type": "move_component", "refdes": "R1", "x": 100, "y": 200}]}
            ),
            encoding="utf-8",
        )
        args.extend(["--file", str(changes)])
    if fake_handler:

        def handler(options):
            assert options["dry_run"] is True
            preview = {"changes": []}
            if command == ("schematic", "edit"):
                preview["save_metadata"] = freeze_save_metadata(snapshot)
            return {
                "preview": preview,
                "confirm_token": "offline-private-token",
            }

        monkeypatch.setattr(driver, "_handler", lambda command: handler)
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        output = cli.run(args)
    return output, project, calls


def test_public_schematic_check_dry_run_keeps_project_and_record_artifact_outputs(
    tmp_path, monkeypatch
):
    output, project, calls = _public_plan(tmp_path, monkeypatch)
    assert output.envelope["ok"], output.envelope
    plan = output.envelope["data"]["preview"]
    assert set(plan["effects"]) == {"change", "artifact"}
    paths = {row["path"]: row["effect"] for row in plan["outputs"]}
    assert paths[str(project.parent)] == "artifact"
    assert paths[str(config_dir() / "checks")] == "artifact"
    assert set(paths.values()) <= set(plan["effects"])
    assert "verify" not in calls
    assert not (config_dir() / "checks").exists()
    assert not (project.parent / "LogFiles").exists()


@pytest.mark.parametrize("command", [("schematic", "edit"), ("schematic", "package")])
def test_engineering_persist_commands_keep_their_persist_output(tmp_path, monkeypatch, command):
    output, project, calls = _public_plan(tmp_path, monkeypatch, command, fake_handler=True)
    assert output.envelope["ok"], output.envelope
    plan = output.envelope["data"]["preview"]
    assert "persist" in plan["effects"]
    assert {row["path"]: row["effect"] for row in plan["outputs"]}[str(project.parent)] == "persist"
    assert "apply_changeset" not in calls and "package" not in calls
