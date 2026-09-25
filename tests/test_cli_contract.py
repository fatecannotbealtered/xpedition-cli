from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*args: str, config_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # These contract tests describe the CLI, not the machine it runs on. NativeBackend
    # finds its adapter through XPEDITION_NATIVE_COMMAND or, failing that, through
    # `xpedition-native-adapter` on PATH — so on a workstation with the native extra
    # installed it is genuinely available, and the write-gate test saw a real snapshot
    # (exit 0) where it asserts E_BACKEND_UNAVAILABLE. The suite passed on CI and failed
    # on the one machine that could actually drive Layout. Point the variable at a path
    # that does not exist so the backend is unavailable either way; `test_native_adapter`
    # sets it to a real adapter itself when it wants one.
    env["XPEDITION_NATIVE_COMMAND"] = str(ROOT / "tests" / "no-such-native-adapter")
    if config_dir:
        env["XPEDITION_CLI_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        [sys.executable, "-m", "xpedition_cli.main", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict:
    value = json.loads(result.stdout)
    assert value["schema_version"] == "1.0"
    assert "duration_ms" in value["meta"]
    return value


def test_self_description_and_system_commands(tmp_path: Path) -> None:
    for args in (
        ("context", "--compact"),
        ("doctor", "--compact"),
        ("system", "doctor", "--compact"),
        ("reference", "--compact"),
        ("changelog", "--compact"),
        ("version", "--compact"),
        ("system", "version", "--compact"),
        ("system", "capabilities", "--compact"),
        ("system", "license", "--compact"),
        ("session", "status", "--compact"),
        ("session", "logs", "--compact"),
    ):
        result = run_cli(*args, config_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        assert payload(result)["ok"] is True
    doctor = payload(run_cli("doctor", "--compact", config_dir=tmp_path))
    assert doctor["data"]["checks"][-1]["check"] == "release_readiness"


def test_project_snapshot_design_snapshot_review_and_bom(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    for args in (
        ("project", "info", "--backend", "mock", "--project", str(project)),
        ("project", "snapshot", "--backend", "mock", "--project", str(project)),
        ("project", "tree", "--backend", "mock", "--project", str(project)),
        ("design", "snapshot", "--backend", "mock", "--project", str(project)),
        ("review", "run", "--backend", "mock", "--project", str(project)),
        ("bom", "export", "--backend", "mock", "--project", str(project)),
    ):
        result = run_cli(*args, config_dir=tmp_path / "config")
        assert result.returncode == 0, result.stderr
        assert payload(result)["ok"] is True
    snapshot_result = run_cli(
        "project",
        "snapshot",
        "--backend",
        "mock",
        "--project",
        str(project),
        "--fields",
        "project,components,_untrusted",
        "--compact",
    )
    snapshot_data = payload(snapshot_result)["data"]
    assert set(snapshot_data) == {"project", "components", "_untrusted"}


def test_changeset_validation_preview_and_single_use_confirmation(tmp_path: Path) -> None:
    changeset = tmp_path / "changeset.json"
    changeset.write_text(
        json.dumps(
            {
                "project": "demo_board",
                "base_revision": "R00",
                "operations": [
                    {
                        "type": "place_component",
                        "refdes": "R1",
                        "part_number": "RES-10K",
                        "x": 100,
                        "y": 80,
                    },
                    {
                        "type": "place_component",
                        "refdes": "C1",
                        "part_number": "CAP-100N",
                        "x": 120,
                        "y": 80,
                    },
                    {"type": "create_net", "name": "3V3"},
                    {"type": "connect", "net": "3V3", "pins": ["R1.1", "C1.1"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    project = tmp_path / "project.json"
    config = tmp_path / "config"
    valid = run_cli("change", "validate", "--changeset", str(changeset), config_dir=config)
    assert valid.returncode == 0
    assert payload(valid)["data"]["valid"] is True
    preview = run_cli(
        "change",
        "preview",
        str(changeset),
        "--backend",
        "mock",
        "--project",
        str(project),
        config_dir=config,
    )
    assert preview.returncode == 0
    preview_data = payload(preview)["data"]
    assert preview_data["confirm_token"].startswith("ct_")
    dry_run = run_cli(
        "change",
        "apply",
        "--backend",
        "mock",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--dry-run",
        config_dir=config,
    )
    token = payload(dry_run)["data"]["confirm_token"]
    cross_command = run_cli(
        "schematic",
        "apply",
        "--backend",
        "mock",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--confirm",
        token,
        config_dir=config,
    )
    assert cross_command.returncode == 6
    assert payload(cross_command)["error"]["code"] == "E_CONFLICT"
    project.write_text(json.dumps({"project": "demo_board"}), encoding="utf-8")
    applied = run_cli(
        "change",
        "apply",
        "--backend",
        "mock",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--confirm",
        token,
        config_dir=config,
    )
    assert applied.returncode == 0, applied.stderr
    applied_data = payload(applied)["data"]
    assert applied_data["verification"]["valid"] is True
    assert Path(applied_data["backup_path"]).exists()
    diff = run_cli("project", "diff", "--project", str(project), config_dir=config)
    assert diff.returncode == 0
    assert payload(diff)["data"]["has_backup"] is True
    assert payload(diff)["data"]["changed"]
    audit = (config / "audit.jsonl").read_text(encoding="utf-8")
    assert token not in audit
    assert "<redacted>" in audit
    history = run_cli(
        "change",
        "history",
        "--project",
        str(project),
        config_dir=config,
    )
    assert history.returncode == 0
    assert payload(history)["data"]["count"] == 1
    rollback_dry = run_cli(
        "change",
        "rollback",
        "--project",
        str(project),
        "--dry-run",
        config_dir=config,
    )
    assert rollback_dry.returncode == 0
    rollback_token = payload(rollback_dry)["data"]["confirm_token"]
    rollback = run_cli(
        "change",
        "rollback",
        "--project",
        str(project),
        "--confirm",
        rollback_token,
        config_dir=config,
    )
    assert rollback.returncode == 0
    assert payload(rollback)["data"]["revision"] == "R00"
    assert json.loads(project.read_text(encoding="utf-8"))["revision"] == "R00"
    replay = run_cli(
        "change",
        "apply",
        "--backend",
        "mock",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--confirm",
        token,
        config_dir=config,
    )
    assert replay.returncode == 6
    assert payload(replay)["error"]["code"] == "E_CONFLICT"


def test_write_gate_and_invalid_backend(tmp_path: Path) -> None:
    changeset = tmp_path / "changeset.json"
    changeset.write_text(
        json.dumps({"project": "demo", "operations": [{"type": "create_net", "name": "3V3"}]}),
        encoding="utf-8",
    )
    result = run_cli(
        "change",
        "apply",
        "--project",
        str(tmp_path / "p.json"),
        "--changeset",
        str(changeset),
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 5
    assert payload(result)["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert not (tmp_path / "p.json").exists()
    schematic_gate = run_cli(
        "schematic",
        "apply",
        "--project",
        str(tmp_path / "schematic.json"),
        "--changeset",
        str(changeset),
        config_dir=tmp_path / "config",
    )
    assert schematic_gate.returncode == 5
    assert payload(schematic_gate)["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    native = run_cli(
        "project", "snapshot", "--backend", "native_xpedition", config_dir=tmp_path / "config"
    )
    assert native.returncode in {3, 4}
    assert payload(native)["error"]["code"] in {"E_NOT_FOUND", "E_BACKEND_UNAVAILABLE"}
    dangerous = run_cli("project", "snapshot", "--dangerous", config_dir=tmp_path / "config")
    assert dangerous.returncode == 2
    assert payload(dangerous)["error"]["code"] == "E_USAGE"


def test_changeset_rejects_revision_and_state_drift(tmp_path: Path) -> None:
    changeset = tmp_path / "changeset.json"
    changeset.write_text(
        json.dumps(
            {
                "project": "drift-project",
                "base_revision": "R00",
                "operations": [{"type": "create_net", "name": "3V3"}],
            }
        ),
        encoding="utf-8",
    )
    project = tmp_path / "project.json"
    config = tmp_path / "config"
    project.write_text(
        json.dumps({"project": "drift-project", "revision": "R02"}), encoding="utf-8"
    )
    stale = run_cli(
        "change",
        "apply",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--dry-run",
        config_dir=config,
    )
    assert stale.returncode == 6
    assert payload(stale)["error"]["code"] == "E_CONFLICT"

    project.write_text(
        json.dumps({"project": "drift-project", "revision": "R00"}), encoding="utf-8"
    )
    dry_run = run_cli(
        "change",
        "apply",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--dry-run",
        config_dir=config,
    )
    token = payload(dry_run)["data"]["confirm_token"]
    project.write_text(
        json.dumps({"project": "drift-project", "revision": "R00", "metadata": {"changed": True}}),
        encoding="utf-8",
    )
    drifted = run_cli(
        "change",
        "apply",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--confirm",
        token,
        config_dir=config,
    )
    assert drifted.returncode == 6
    assert payload(drifted)["error"]["code"] == "E_CONFLICT"


def test_unknown_command_is_structured(tmp_path: Path) -> None:
    result = run_cli("does-not-exist", config_dir=tmp_path / "config")
    assert result.returncode == 2
    assert payload(result)["error"]["code"] == "E_USAGE"


def test_format_aliases_changelog_delta_and_bom_pagination(tmp_path: Path) -> None:
    config = tmp_path / "config"
    json_alias = run_cli("context", "--json", "--compact", config_dir=config)
    assert json_alias.returncode == 0
    assert payload(json_alias)["ok"] is True
    text_result = run_cli("project", "info", "--format", "text", config_dir=config)
    assert text_result.returncode == 0
    assert not text_result.stdout.lstrip().startswith("{")
    raw_result = run_cli("project", "snapshot", "--format", "raw", "--compact", config_dir=config)
    assert raw_result.returncode == 0
    assert json.loads(raw_result.stdout)["project"] == "mock-project"
    delta = run_cli("changelog", "--since", "0.0.0", config_dir=config)
    assert delta.returncode == 0
    assert payload(delta)["data"]["entries"]

    project = tmp_path / "bom.json"
    project.write_text(
        json.dumps(
            {
                "project": "bom-project",
                "components": [
                    {"refdes": "R1", "internal_part_no": "RES-1"},
                    {"refdes": "R2", "internal_part_no": "RES-2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    page = run_cli("bom", "export", "--project", str(project), "--limit", "1", config_dir=config)
    assert page.returncode == 0
    page_data = payload(page)["data"]
    assert page_data["count"] == 1
    assert page_data["has_more"] is True
    assert page_data["next_offset"] == 1

    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            [
                {"id": "R1", "finding": "first"},
                {"id": "R2", "finding": "second"},
            ]
        ),
        encoding="utf-8",
    )
    review_page = run_cli(
        "review",
        "run",
        "--project",
        str(project),
        "--rules",
        str(rules),
        "--limit",
        "1",
        config_dir=config,
    )
    assert review_page.returncode == 0
    review_data = payload(review_page)["data"]
    assert review_data["has_more"] is True
    assert review_data["next_offset"] == 1


def test_mock_read_domains(tmp_path: Path) -> None:
    project = tmp_path / "rich-project.json"
    project.write_text(
        json.dumps(
            {
                "project": "rich-project",
                "components": [
                    {
                        "refdes": "R1",
                        "internal_part_no": "RES-10K",
                        "pins": [{"number": "1", "name": "1", "type": "passive"}],
                    }
                ],
                "nets": [{"name": "3V3"}],
                "connections": [{"net": "3V3", "pins": ["R1.1", "R1.1"]}],
                "sheets": [{"name": "top"}],
                "pcb": {
                    "components": [{"refdes": "R1"}],
                    "footprints": [{"name": "0603"}],
                    "nets": [{"name": "3V3"}],
                    "layers": [{"name": "TOP"}],
                    "stackup": [{"layer": "TOP"}],
                    "tracks": [{"net": "3V3"}],
                    "vias": [{"net": "3V3"}],
                    "zones": [{"name": "GND"}],
                    "keepouts": [{"name": "edge"}],
                },
                "library": {
                    "parts": [{"part_number": "RES-10K"}],
                    "symbols": [{"name": "R"}],
                    "footprints": [{"name": "0603"}],
                    "padstacks": [{"name": "P1"}],
                    "models": [{"name": "res.step"}],
                },
                "constraints": [{"name": "width", "type": "minimum", "value": "0.2mm"}],
                "analysis": {
                    "erc": [{"severity": "info", "message": "ok"}],
                    "drc": [],
                    "dfm": [],
                    "results": [],
                },
                "manufacturing": {"artifacts": [{"kind": "gerber", "path": "out/top.gbr"}]},
            }
        ),
        encoding="utf-8",
    )
    commands = [
        ("schematic", "sheets"),
        ("schematic", "components"),
        ("schematic", "pins"),
        ("schematic", "nets"),
        ("schematic", "connectivity"),
        ("schematic", "unconnected"),
        ("schematic", "power"),
        ("schematic", "interfaces"),
        ("schematic", "query", "--query", "3V3"),
        ("pcb", "info"),
        ("pcb", "components"),
        ("pcb", "footprints"),
        ("pcb", "nets"),
        ("pcb", "layers"),
        ("pcb", "stackup"),
        ("pcb", "tracks"),
        ("pcb", "vias"),
        ("pcb", "zones"),
        ("pcb", "keepouts"),
        ("pcb", "query", "--query", "3V3"),
        ("constraints", "list"),
        ("constraints", "query", "--query", "width"),
        ("constraints", "validate"),
        ("constraints", "export"),
        ("analysis", "results"),
        ("analysis", "run", "--kind", "all"),
        ("analysis", "erc"),
        ("analysis", "drc"),
        ("analysis", "dfm"),
        ("manufacturing", "artifacts"),
        ("manufacturing", "verify"),
        ("manufacturing", "bom"),
        ("library", "search", "--query", "RES"),
        ("library", "parts"),
        ("library", "symbols"),
        ("library", "footprints"),
        ("library", "padstacks"),
        ("library", "models"),
        ("library", "validate"),
        ("review", "findings"),
        ("review", "report"),
        ("bom", "normalize"),
        ("bom", "group"),
        ("bom", "variants"),
        ("bom", "missing"),
        ("bom", "duplicates"),
        ("bom", "validate"),
        ("agent", "snapshot"),
        ("agent", "query", "--query", "3V3"),
        ("agent", "review"),
        ("agent", "capabilities"),
    ]
    for args in commands:
        result = run_cli(
            *args, "--backend", "mock", "--project", str(project), config_dir=tmp_path / "config"
        )
        assert result.returncode == 0, f"{args}: {result.stderr}"
        assert payload(result)["ok"] is True

    for args in (
        ("schematic", "query"),
        ("pcb", "query"),
        ("constraints", "query"),
        ("library", "search"),
    ):
        missing_query = run_cli(*args, "--project", str(project), config_dir=tmp_path / "config")
        assert missing_query.returncode == 2
        assert payload(missing_query)["error"]["code"] == "E_USAGE"

    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        json.dumps({"project": "bad", "pcb": {"layers": "not-an-array"}}),
        encoding="utf-8",
    )
    invalid_project = run_cli(
        "project", "snapshot", "--project", str(malformed), config_dir=tmp_path / "config"
    )
    assert invalid_project.returncode == 2
    assert payload(invalid_project)["error"]["code"] == "E_PROJECT_INVALID"

    other_project = tmp_path / "other-project.json"
    other_project.write_text(
        json.dumps(
            {"project": "other", "components": [{"refdes": "R2", "internal_part_no": "RES-22K"}]}
        ),
        encoding="utf-8",
    )
    compared = run_cli(
        "bom",
        "compare",
        "--project",
        str(project),
        "--other-project",
        str(other_project),
        config_dir=tmp_path / "config",
    )
    assert compared.returncode == 0
    assert payload(compared)["data"]["added"]


def test_agent_stdio_stream() -> None:
    request_lines = (
        "\n".join(
            [
                json.dumps({"id": 1, "method": "capabilities"}),
                json.dumps({"id": "q1", "method": "query", "params": {"query": "3V3"}}),
                json.dumps({"id": "bad", "method": "unsupported"}),
            ]
        )
        + "\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "xpedition_cli.main", "agent", "serve", "--transport", "stdio"],
        cwd=ROOT,
        input=request_lines,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert [line["type"] for line in lines] == ["result", "result", "error", "summary"]
    assert [line["id"] for line in lines] == ["1", "q1", "bad", None]
    assert all(line["schema_version"] == "1.0" for line in lines)
    assert lines[2]["error"]["code"] == "E_USAGE"
    assert lines[-1]["data"]["count"] == 3


def _assert_needs_native(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 4, result.stdout
    error = payload(result)["error"]
    assert error["code"] == "E_BACKEND_UNAVAILABLE"
    assert "native_xpedition" in json.dumps(error["details"])


def test_native_session_lifecycle_is_explicitly_unavailable(tmp_path: Path) -> None:
    config = tmp_path / "config"
    _assert_needs_native(run_cli("session", "start", config_dir=config))
    _assert_needs_native(run_cli("session", "attach", config_dir=config))
    _assert_needs_native(run_cli("session", "open", config_dir=config))
    _assert_needs_native(run_cli("session", "stop", config_dir=config))


def test_session_lifecycle_is_declared_by_reference(tmp_path: Path) -> None:
    """An agent can only reach these verbs if `reference` admits they exist."""
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    assert result.returncode == 0
    declared = {c["path"]: c for c in payload(result)["data"]["commands"]}
    for path in ("session start", "session attach", "session open", "session stop"):
        assert path in declared, f"{path} is implemented but undeclared"
    stop = declared["session stop"]
    assert stop["permission_tier"] == "write"
    assert "unsaved" in stop["blast_radius"]
    assert stop["dry_run_output_schema"] == "session_stop_preview"


def test_timeout_maps_to_exit_eight_and_is_retryable(tmp_path: Path) -> None:
    """`E_TIMEOUT` is a declared public error code (exit 8, retryable), so the CLI
    boundary has to show it: an adapter that never answers must come back as that
    code and not as a crash or a hang."""
    adapter = tmp_path / "slow_adapter.py"
    adapter.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    if os.name == "nt":
        launcher = tmp_path / "slow.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{adapter}"\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "slow.sh"
        launcher.write_text(f'#!/bin/sh\n"{sys.executable}" "{adapter}"\n', encoding="utf-8")
        launcher.chmod(0o755)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["XPEDITION_CLI_CONFIG_DIR"] = str(tmp_path / "config")
    env["XPEDITION_NATIVE_COMMAND"] = str(launcher)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xpedition_cli.main",
            "session",
            "status",
            "--backend",
            "native_xpedition",
            "--compact",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=180,
    )
    error = payload(result)["error"]
    if error["code"] == "E_TIMEOUT":
        assert result.returncode == 8
        assert error["retryable"] is True
    else:
        # without the native prerequisites the adapter is refused before it is started;
        # the timeout branch itself is covered in test_native_adapter.py
        assert error["code"] in {"E_BACKEND_UNAVAILABLE", "E_CONFIG"}


def test_session_kind_rejects_an_unknown_application(tmp_path: Path) -> None:
    result = run_cli(
        "session",
        "attach",
        "--backend",
        "native_xpedition",
        "--kind",
        "bogus",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2
    error = payload(result)["error"]
    assert error["code"] == "E_VALIDATION"
    assert error["details"]["choices"] == ["designer", "layout", "pcb", "schematic"]


def test_schematic_export_needs_the_native_backend(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    result = run_cli(
        "schematic", "export", "--project", str(project), config_dir=tmp_path / "config"
    )
    _assert_needs_native(result)


def test_schematic_export_is_declared_by_reference(tmp_path: Path) -> None:
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    assert result.returncode == 0
    declared = {c["path"]: c for c in payload(result)["data"]["commands"]}
    export = declared["schematic export"]
    assert export["output_schema"] == "schematic_export"
    assert {p["name"] for p in export["params"]} == {"project", "output", "color", "schematic"}
    assert "never replaced" in export["blast_radius"]


def test_confirm_examples_repeat_a_dry_run_example(tmp_path: Path) -> None:
    # The confirm token is bound to the arguments, so a confirm example whose
    # arguments no dry-run example shares fails with E_CONFLICT when copied.
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    assert result.returncode == 0

    def arguments(example: str) -> list[str]:
        kept: list[str] = []
        skip = False
        for word in shlex.split(example):
            if skip:
                skip = False
            elif word == "--confirm":
                skip = True
            elif word not in ("--dry-run", "--compact"):
                kept.append(word)
        return kept

    for command in payload(result)["data"]["commands"]:
        dry_runs = [arguments(e) for e in command["examples"] if "--dry-run" in e]
        for example in command["examples"]:
            if "--confirm" in example:
                assert arguments(example) in dry_runs, (command["path"], example)


def test_pcb_arrange_declares_that_it_deletes_the_routing(tmp_path: Path) -> None:
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    assert result.returncode == 0
    declared = {c["path"]: c for c in payload(result)["data"]["commands"]}
    assert "every trace and via" in declared["pcb arrange"]["blast_radius"]


EXAMPLE_DESIGN = ROOT / "examples" / "demo-sensor-board.json"


def test_schematic_draw_dry_run_plans_offline(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    result = run_cli(
        "schematic",
        "draw",
        "--project",
        str(project),
        "--design",
        str(EXAMPLE_DESIGN),
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 0, result.stdout
    data = payload(result)["data"]
    assert data["confirm_token"]
    summary = data["preview"]["summary"]
    assert summary["parts"] == 30
    assert [sheet["number"] for sheet in summary["sheets"]] == [1, 2, 3, 4]
    assert summary["issues"] == []
    assert data["preview"]["changes"][1]["action"] == "wipe_and_draw_sheet"
    assert "GND" in summary["nets"] and "+3V3" in summary["nets"]


def test_schematic_draw_confirm_needs_the_native_backend(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    result = run_cli(
        "schematic",
        "draw",
        "--project",
        str(project),
        "--design",
        str(EXAMPLE_DESIGN),
        "--confirm",
        "ct_bogus",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(result)


def test_schematic_draw_rejects_an_undrawable_design(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "sheets": [
                    {
                        "number": 1,
                        "blocks": [{"kind": "ladder", "x": 100, "y": 100, "path": ["gnd"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "schematic",
        "draw",
        "--project",
        str(project),
        "--design",
        str(bad),
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2, result.stdout
    assert payload(result)["error"]["code"] == "E_VALIDATION"


def test_schematic_draw_is_declared_by_reference(tmp_path: Path) -> None:
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    declared = {c["path"]: c for c in payload(result)["data"]["commands"]}
    draw = declared["schematic draw"]
    assert draw["permission_tier"] == "write"
    assert draw["dry_run_output_schema"] == "schematic_draw_preview"
    assert {p["name"] for p in draw["params"]} == {"project", "design"}
    assert "wiped and redrawn" in draw["blast_radius"]


def test_schematic_show_needs_the_native_backend(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    result = run_cli(
        "schematic",
        "show",
        "--project",
        str(project),
        "--sheet",
        "2",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(result)


def test_schematic_show_validates_sheet_and_output(tmp_path: Path) -> None:
    result = run_cli(
        "schematic",
        "show",
        "--project",
        "./demo.prj",
        "--sheet",
        "zero",
        "--backend",
        "native_xpedition",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2
    assert payload(result)["error"]["code"] == "E_VALIDATION"
    result = run_cli(
        "schematic",
        "show",
        "--project",
        "./demo.prj",
        "--output",
        "./shot.bmp",
        "--backend",
        "native_xpedition",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2
    assert payload(result)["error"]["code"] == "E_VALIDATION"


def test_schematic_show_is_declared_by_reference(tmp_path: Path) -> None:
    result = run_cli("reference", "--compact", config_dir=tmp_path / "config")
    declared = {c["path"]: c for c in payload(result)["data"]["commands"]}
    show = declared["schematic show"]
    assert show["output_schema"] == "schematic_show"
    assert {p["name"] for p in show["params"]} == {"project", "sheet", "output"}


def test_schematic_export_rejects_a_non_pdf_output(tmp_path: Path) -> None:
    result = run_cli(
        "schematic",
        "export",
        "--project",
        "./demo.prj",
        "--output",
        "./demo.svg",
        "--backend",
        "native_xpedition",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2
    assert payload(result)["error"]["code"] == "E_VALIDATION"


def test_exchange_inspect_and_guarded_import(tmp_path: Path) -> None:
    source = tmp_path / "bom.csv"
    source.write_text(
        "refdes,part_number,mpn,value,package\nR1,RES-10K,RES-10K,10k,0603\n",
        encoding="utf-8",
    )
    config = tmp_path / "config"
    inspected = run_cli("exchange", "inspect", "--input", str(source), config_dir=config)
    assert inspected.returncode == 0
    inspected_data = payload(inspected)["data"]
    assert inspected_data["format"] == "csv"
    assert inspected_data["record_count"] == 1

    target = tmp_path / "imported.json"
    dry_run = run_cli(
        "exchange",
        "import",
        "--input",
        str(source),
        "--project",
        str(target),
        "--dry-run",
        config_dir=config,
    )
    assert dry_run.returncode == 0
    token = payload(dry_run)["data"]["confirm_token"]
    applied = run_cli(
        "exchange",
        "import",
        "--input",
        str(source),
        "--project",
        str(target),
        "--confirm",
        token,
        config_dir=config,
    )
    assert applied.returncode == 0, applied.stderr
    assert payload(applied)["data"]["verification"]["valid"] is True
    snapshot = run_cli("project", "snapshot", "--project", str(target), config_dir=config)
    assert payload(snapshot)["data"]["components"][0]["refdes"] == "R1"

    unsupported = tmp_path / "design.pdf"
    unsupported.write_bytes(b"not parsed")
    rejected = run_cli("exchange", "inspect", "--input", str(unsupported), config_dir=config)
    assert rejected.returncode == 4
    assert payload(rejected)["error"]["code"] == "E_BACKEND_UNAVAILABLE"

    ipc = tmp_path / "design.ipc2581"
    ipc.write_text(
        "<IPC-2581><Component refDes='R1' partName='RES-10K' />"
        "<Net name='3V3'><Pin componentRef='R1' pin='1' /></Net></IPC-2581>",
        encoding="utf-8",
    )
    ipc_result = run_cli("exchange", "inspect", "--input", str(ipc), config_dir=config)
    assert ipc_result.returncode == 0
    ipc_data = payload(ipc_result)["data"]
    assert ipc_data["format"] == "ipc2581"
    assert ipc_data["record_count"] == 1
    assert ipc_data["project"]["nets"][0]["name"] == "3V3"
    assert ipc_data["project"]["connections"][0]["pins"] == ["R1.1"]


def test_project_init_is_guarded_and_idempotency_safe(tmp_path: Path) -> None:
    project = tmp_path / "new-project.json"
    config = tmp_path / "config"
    required = run_cli("project", "init", "--project", str(project), config_dir=config)
    assert required.returncode == 5
    assert payload(required)["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    dry_run = run_cli(
        "project",
        "init",
        "--project",
        str(project),
        "--name",
        "demo_board",
        "--dry-run",
        config_dir=config,
    )
    assert dry_run.returncode == 0
    token = payload(dry_run)["data"]["confirm_token"]
    assert not project.exists()
    created = run_cli(
        "project",
        "init",
        "--project",
        str(project),
        "--name",
        "demo_board",
        "--confirm",
        token,
        config_dir=config,
    )
    assert created.returncode == 0, created.stderr
    assert payload(created)["data"]["created"] is True
    assert json.loads(project.read_text(encoding="utf-8"))["project"] == "demo_board"
    existing = run_cli("project", "init", "--project", str(project), "--dry-run", config_dir=config)
    assert existing.returncode == 6
    assert payload(existing)["error"]["code"] == "E_CONFLICT"


def test_pcb_changeset_operations_are_verified(tmp_path: Path) -> None:
    project = tmp_path / "pcb-project.json"
    project.write_text(
        json.dumps({"project": "pcb-project", "pcb": {"nets": [{"name": "3V3"}]}}),
        encoding="utf-8",
    )
    changeset = tmp_path / "pcb-changeset.json"
    changeset.write_text(
        json.dumps(
            {
                "project": "pcb-project",
                "base_revision": "R00",
                "operations": [
                    {
                        "type": "place_pcb_component",
                        "refdes": "U1",
                        "footprint": "QFN",
                        "x": 1,
                        "y": 2,
                    },
                    {"type": "move_pcb_component", "refdes": "U1", "x": 3, "y": 4},
                    {
                        "type": "create_track",
                        "net": "3V3",
                        "layer": "TOP",
                        "points": [[0, 0], [3, 4]],
                    },
                    {"type": "create_via", "net": "3V3", "x": 3, "y": 4},
                    {
                        "type": "create_zone",
                        "name": "GND",
                        "layer": "BOTTOM",
                        "polygon": [[0, 0], [1, 0], [1, 1]],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config"
    dry_run = run_cli(
        "change",
        "apply",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--dry-run",
        config_dir=config,
    )
    token = payload(dry_run)["data"]["confirm_token"]
    applied = run_cli(
        "change",
        "apply",
        "--project",
        str(project),
        "--changeset",
        str(changeset),
        "--confirm",
        token,
        config_dir=config,
    )
    assert applied.returncode == 0, applied.stderr
    snapshot = json.loads(project.read_text(encoding="utf-8"))
    assert snapshot["pcb"]["components"][0]["x"] == 3
    assert len(snapshot["pcb"]["tracks"]) == 1
    assert len(snapshot["pcb"]["vias"]) == 1
    assert len(snapshot["pcb"]["zones"]) == 1


def test_project_init_template_is_native_only_and_validated(tmp_path: Path) -> None:
    config = tmp_path / "config"
    target = tmp_path / "new" / "New.prj"
    template = tmp_path / "Tpl.prj"
    mock = run_cli(
        "project",
        "init",
        "--project",
        str(target),
        "--template",
        str(template),
        "--dry-run",
        config_dir=config,
    )
    assert mock.returncode == 4
    assert payload(mock)["error"]["code"] == "E_BACKEND_UNAVAILABLE"
    missing = run_cli(
        "project",
        "init",
        "--backend",
        "native_xpedition",
        "--project",
        str(target),
        "--template",
        str(template),
        "--dry-run",
        config_dir=config,
    )
    assert missing.returncode == 3
    assert payload(missing)["error"]["code"] == "E_NOT_FOUND"
    template.write_text("SECTION DesignInfo", encoding="utf-8")
    dry_run = run_cli(
        "project",
        "init",
        "--backend",
        "native_xpedition",
        "--project",
        str(target),
        "--template",
        str(template),
        "--dry-run",
        config_dir=config,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    data = payload(dry_run)["data"]
    assert data["preview"]["changes"][0]["action"] == "copy_project_folder"
    assert data["preview"]["template"] == str(template.resolve())
    assert data["confirm_token"]
    assert not target.parent.exists()


def test_project_init_template_rejects_a_non_ascii_destination(tmp_path: Path) -> None:
    template = tmp_path / "Tpl.prj"
    template.write_text("SECTION DesignInfo", encoding="utf-8")
    target = tmp_path / "新工程" / "New.prj"
    result = run_cli(
        "project",
        "init",
        "--backend",
        "native_xpedition",
        "--project",
        str(target),
        "--template",
        str(template),
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 2
    assert payload(result)["error"]["code"] == "E_VALIDATION"


def test_library_build_dry_run_plans_offline_and_confirm_needs_native(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    design = ROOT / "examples" / "demo-sensor-board.json"
    result = run_cli(
        "library",
        "build",
        "--project",
        str(project),
        "--design",
        str(design),
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 0, result.stdout
    data = payload(result)["data"]
    preview = data["preview"]
    assert preview["partition"] == "PartQuest"
    assert preview["parts"] > 0 and preview["cells"] > 0 and preview["padstacks"] > 0
    assert preview["summary"]["issues"] == []
    assert data["confirm_token"]
    confirm = run_cli(
        "library",
        "build",
        "--project",
        str(project),
        "--design",
        str(design),
        "--confirm",
        "ct_bogus",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(confirm)


_BOARD_PRJ = (
    "SECTION DesignInfo\n"
    'KEY CentralLibrary "RcLib\\TemplateLibrary.lmc"\n'
    "ENDSECTION\n"
    "SECTION iCDB\n"
    "LIST Designs\n"
    'VALUE "Schematic1"\n'
    'VALUE "Board1"\n'
    "ENDLIST\n"
    "ENDSECTION\n"
    "SECTION Schematic1\n"
    'KEY ConfigType "Board1"\n'
    "ENDSECTION\n"
    "SECTION Board1\n"
    "LIST PDBs\n"
    'VALUE "PartsDBLibs\\PartQuest.pdb"\n'
    "ENDLIST\n"
    'KEY ConfigType "PCB"\n'
    'KEY PCBDesignPath "{pcb}"\n'
    "ENDSECTION\n"
)


def test_pcb_create_previews_the_board_design_and_confirm_needs_native(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb=""), encoding="utf-8")
    result = run_cli(
        "pcb",
        "create",
        "--project",
        str(project),
        "--template",
        "8 Layer Template",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert result.returncode == 0, result.stdout
    data = payload(result)["data"]
    preview = data["preview"]
    assert preview["design"] == "Board1"
    assert preview["template"] == "8 Layer Template"
    assert preview["pcb"] == "PCB\\demo.pcb"
    actions = [change["action"] for change in preview["changes"]]
    assert actions == [
        "ensure_layout_template",
        "list_design_first",
        "run_jobwizard",
        "register_cells_in_project",
    ]
    assert data["confirm_token"]
    confirm = run_cli(
        "pcb",
        "create",
        "--project",
        str(project),
        "--template",
        "8 Layer Template",
        "--confirm",
        "ct_bogus",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(confirm)
    gate = run_cli("pcb", "create", "--project", str(project), config_dir=tmp_path / "config")
    assert gate.returncode == 5
    assert payload(gate)["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    bad_name = run_cli(
        "pcb",
        "create",
        "--project",
        str(project),
        "--name",
        "bad name",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(bad_name)["error"]["code"] == "E_VALIDATION"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    taken = run_cli(
        "pcb", "create", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    assert taken.returncode == 6
    assert payload(taken)["error"]["code"] == "E_CONFLICT"


def test_pcb_annotate_previews_the_board_and_confirm_needs_native(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb=""), encoding="utf-8")
    missing = run_cli(
        "pcb", "annotate", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    assert missing.returncode == 3
    assert payload(missing)["error"]["code"] == "E_NOT_FOUND"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    result = run_cli(
        "pcb", "annotate", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    assert result.returncode == 0, result.stdout
    data = payload(result)["data"]
    assert data["preview"]["pcb"].endswith("demo.pcb")
    assert [change["action"] for change in data["preview"]["changes"]] == [
        "open_board",
        "forward_annotate",
        "save_board",
    ]
    confirm = run_cli(
        "pcb",
        "annotate",
        "--project",
        str(project),
        "--confirm",
        "ct_bogus",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(confirm)
    board = tmp_path / "board.pcb"
    board.write_bytes(b"")
    direct = run_cli(
        "pcb", "annotate", "--project", str(board), "--dry-run", config_dir=tmp_path / "config"
    )
    assert direct.returncode == 0, direct.stdout
    assert payload(direct)["data"]["preview"]["pcb"] == str(board.resolve())


def test_pcb_arrange_needs_layout_for_the_plan_and_the_gate_first(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    gate = run_cli("pcb", "arrange", "--project", str(project), config_dir=tmp_path / "config")
    assert gate.returncode == 5
    assert payload(gate)["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    dry = run_cli(
        "pcb", "arrange", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    _assert_needs_native(dry)
    confirm = run_cli(
        "pcb",
        "arrange",
        "--project",
        str(project),
        "--all",
        "--confirm",
        "ct_bogus",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(confirm)
    missing = run_cli(
        "pcb",
        "arrange",
        "--project",
        str(tmp_path / "gone.prj"),
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert missing.returncode == 3


def test_pcb_show_needs_layout_and_a_png_output(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    result = run_cli("pcb", "show", "--project", str(project), config_dir=tmp_path / "config")
    _assert_needs_native(result)
    bad = run_cli(
        "pcb",
        "show",
        "--project",
        str(project),
        "--output",
        str(tmp_path / "board.jpg"),
        config_dir=tmp_path / "config",
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    missing = run_cli(
        "pcb", "show", "--project", str(tmp_path / "gone.prj"), config_dir=tmp_path / "config"
    )
    assert missing.returncode == 3


def test_pcb_outline_and_pour_gate_and_validate_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    usage = run_cli(
        "pcb", "outline", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    assert payload(usage)["error"]["code"] == "E_USAGE"
    gate = run_cli(
        "pcb",
        "outline",
        "--project",
        str(project),
        "--width",
        "45",
        "--height",
        "30",
        config_dir=tmp_path / "config",
    )
    assert gate.returncode == 5
    dry = run_cli(
        "pcb",
        "outline",
        "--project",
        str(project),
        "--width",
        "45",
        "--height",
        "30",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(dry)
    bad = run_cli(
        "pcb",
        "pour",
        "--project",
        str(project),
        "--layer",
        "0",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    pour_gate = run_cli("pcb", "pour", "--project", str(project), config_dir=tmp_path / "config")
    assert pour_gate.returncode == 5
    pour = run_cli(
        "pcb",
        "pour",
        "--project",
        str(project),
        "--net",
        "GND",
        "--layer",
        "2",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(pour)


def test_pcb_route_gates_and_needs_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    gate = run_cli("pcb", "route", "--project", str(project), config_dir=tmp_path / "config")
    assert gate.returncode == 5
    dry = run_cli(
        "pcb",
        "route",
        "--project",
        str(project),
        "--passes",
        "route:1-5,smooth",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(dry)


def test_pcb_drc_needs_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    result = run_cli("pcb", "drc", "--project", str(project), config_dir=tmp_path / "config")
    _assert_needs_native(result)
    missing = run_cli(
        "pcb", "drc", "--project", str(tmp_path / "gone.prj"), config_dir=tmp_path / "config"
    )
    assert missing.returncode == 3


def test_pcb_holes_gate_and_validate_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    bad = run_cli(
        "pcb",
        "holes",
        "--project",
        str(project),
        "--diameter",
        "0",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    gate = run_cli("pcb", "holes", "--project", str(project), config_dir=tmp_path / "config")
    assert gate.returncode == 5
    dry = run_cli(
        "pcb", "holes", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    _assert_needs_native(dry)
    rounded = run_cli(
        "pcb",
        "outline",
        "--project",
        str(project),
        "--width",
        "60",
        "--height",
        "45",
        "--radius",
        "30",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(rounded)["error"]["code"] == "E_VALIDATION"


def test_pcb_export_gate_and_validate_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    bad = run_cli(
        "pcb",
        "export",
        "--project",
        str(project),
        "--formats",
        "pdf",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    gate = run_cli("pcb", "export", "--project", str(project), config_dir=tmp_path / "config")
    assert gate.returncode == 5
    dry = run_cli(
        "pcb", "export", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    _assert_needs_native(dry)


def test_pcb_rules_gate_and_validate_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    usage = run_cli(
        "pcb", "rules", "--project", str(project), "--dry-run", config_dir=tmp_path / "config"
    )
    assert payload(usage)["error"]["code"] == "E_USAGE"
    bad = run_cli(
        "pcb",
        "rules",
        "--project",
        str(project),
        "--class",
        "POWER",
        "--width",
        "0",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    gate = run_cli(
        "pcb",
        "rules",
        "--project",
        str(project),
        "--class",
        "POWER",
        "--width",
        "0.5",
        config_dir=tmp_path / "config",
    )
    assert gate.returncode == 5
    dry = run_cli(
        "pcb",
        "rules",
        "--project",
        str(project),
        "--class",
        "POWER",
        "--nets",
        "VBAT,+3V3",
        "--width",
        "0.5",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(dry)


def test_pcb_render_validates_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    usage = run_cli("pcb", "render", "--project", str(project), config_dir=tmp_path / "config")
    assert payload(usage)["error"]["code"] == "E_USAGE"
    bad_side = run_cli(
        "pcb",
        "render",
        "--project",
        str(project),
        "--output",
        str(tmp_path / "b.png"),
        "--side",
        "inner",
        config_dir=tmp_path / "config",
    )
    assert payload(bad_side)["error"]["code"] == "E_VALIDATION"
    needs = run_cli(
        "pcb",
        "render",
        "--project",
        str(project),
        "--output",
        str(tmp_path / "b.png"),
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(needs)


def test_hand_routing_commands_validate_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    config = tmp_path / "config"
    # pcb trace: points are checked before anything touches Layout
    bad = run_cli(
        "pcb",
        "trace",
        "--project",
        str(project),
        "--net",
        "A",
        "--points",
        "1,1",
        "--dry-run",
        config_dir=config,
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    gate = run_cli(
        "pcb",
        "trace",
        "--project",
        str(project),
        "--net",
        "A",
        "--points",
        "1,1 2,2",
        config_dir=config,
    )
    assert gate.returncode == 5
    _assert_needs_native(
        run_cli(
            "pcb",
            "trace",
            "--project",
            str(project),
            "--net",
            "A",
            "--points",
            "1,1 2,2",
            "--dry-run",
            config_dir=config,
        )
    )
    missing_plan = run_cli(
        "pcb",
        "trace",
        "--project",
        str(project),
        "--file",
        str(tmp_path / "none.json"),
        "--dry-run",
        config_dir=config,
    )
    assert missing_plan.returncode == 3
    plan = tmp_path / "routes.json"
    plan.write_text(
        '{"traces": [{"net": "A", "points": "0,0 1,0"}], "vias": [{"net": "A", "at": [1, 0]}]}',
        encoding="utf-8",
    )
    _assert_needs_native(
        run_cli(
            "pcb",
            "trace",
            "--project",
            str(project),
            "--file",
            str(plan),
            "--dry-run",
            config_dir=config,
        )
    )
    # pcb via
    usage = run_cli(
        "pcb", "via", "--project", str(project), "--net", "A", "--dry-run", config_dir=config
    )
    assert payload(usage)["error"]["code"] == "E_USAGE"
    _assert_needs_native(
        run_cli(
            "pcb",
            "via",
            "--project",
            str(project),
            "--net",
            "A",
            "--at",
            "1,2",
            "--dry-run",
            config_dir=config,
        )
    )
    # pcb unroute
    usage = run_cli("pcb", "unroute", "--project", str(project), "--dry-run", config_dir=config)
    assert payload(usage)["error"]["code"] == "E_USAGE"
    assert (
        run_cli("pcb", "unroute", "--project", str(project), "--all", config_dir=config).returncode
        == 5
    )
    _assert_needs_native(
        run_cli(
            "pcb",
            "unroute",
            "--project",
            str(project),
            "--nets",
            "A,B",
            "--dry-run",
            config_dir=config,
        )
    )
    # --pace (the wait between placed items) is validated before the backend
    slow = run_cli(
        "pcb",
        "trace",
        "--project",
        str(project),
        "--net",
        "SCL",
        "--points",
        "1,1 2,2",
        "--pace",
        "-1",
        "--dry-run",
        config_dir=config,
    )
    assert payload(slow)["error"]["code"] == "E_VALIDATION"
    # pcb move
    usage = run_cli(
        "pcb", "move", "--project", str(project), "--refdes", "U1", "--dry-run", config_dir=config
    )
    assert payload(usage)["error"]["code"] == "E_USAGE"
    bad_rotate = run_cli(
        "pcb",
        "move",
        "--project",
        str(project),
        "--refdes",
        "U1",
        "--to",
        "1,2",
        "--rotate",
        "x",
        "--dry-run",
        config_dir=config,
    )
    assert payload(bad_rotate)["error"]["code"] == "E_VALIDATION"
    _assert_needs_native(
        run_cli(
            "pcb",
            "move",
            "--project",
            str(project),
            "--refdes",
            "U1",
            "--to",
            "1,2",
            "--rotate",
            "90",
            "--dry-run",
            config_dir=config,
        )
    )
    # pcb geometry
    bad_output = run_cli(
        "pcb",
        "geometry",
        "--project",
        str(project),
        "--output",
        str(tmp_path / "g.txt"),
        config_dir=config,
    )
    assert payload(bad_output)["error"]["code"] == "E_VALIDATION"
    _assert_needs_native(run_cli("pcb", "geometry", "--project", str(project), config_dir=config))


def test_pcb_labels_gate_and_validate_before_layout(tmp_path: Path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\\\demo.pcb"), encoding="utf-8")
    config = tmp_path / "config"
    bad = run_cli(
        "pcb", "labels", "--project", str(project), "--gap", "x", "--dry-run", config_dir=config
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    assert run_cli("pcb", "labels", "--project", str(project), config_dir=config).returncode == 5
    _assert_needs_native(
        run_cli("pcb", "labels", "--project", str(project), "--dry-run", config_dir=config)
    )
    # pcb unroute --at wants a point
    _assert_needs_native(
        run_cli(
            "pcb",
            "unroute",
            "--project",
            str(project),
            "--at",
            "1,2",
            "--layer",
            "4",
            "--dry-run",
            config_dir=config,
        )
    )
    bad_layer = run_cli(
        "pcb",
        "unroute",
        "--project",
        str(project),
        "--at",
        "1,2",
        "--layer",
        "x",
        "--dry-run",
        config_dir=config,
    )
    assert payload(bad_layer)["error"]["code"] == "E_VALIDATION"


def test_pcb_stitch_and_the_offline_plan_check(tmp_path: Path) -> None:
    import json as _json

    geometry = tmp_path / "board.json"
    geometry.write_text(
        _json.dumps(
            {
                "layers": 4,
                "outline": [{"path": [[0, 0, 0], [40, 0, 0], [40, 30, 0], [0, 30, 0]]}],
                "pads": [
                    {"circle": [10.0, 10.0, 0.5], "layer": 1, "net": "GND", "refdes": "C1"},
                    {"circle": [20.0, 10.0, 0.5], "layer": 1, "net": "A", "refdes": "C1"},
                ],
                "traces": [],
                "vias": [],
                "components": [
                    {
                        "refdes": "C1",
                        "pins": [
                            {"pin": "1", "net": "GND", "x": 10.0, "y": 10.0},
                            {"pin": "2", "net": "A", "x": 20.0, "y": 10.0},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = tmp_path / "stitch.json"
    result = run_cli(
        "pcb",
        "stitch",
        "--geometry",
        str(geometry),
        "--output",
        str(plan),
        config_dir=tmp_path / "config",
    )
    data = payload(result)["data"]
    assert result.returncode == 0 and data["vias"] == 1 and data["without_room"] == []
    assert _json.loads(plan.read_text(encoding="utf-8"))["items"]
    project = tmp_path / "demo.prj"
    project.write_text(_BOARD_PRJ.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    # a plan through the other pad is refused before Layout is touched
    bad = run_cli(
        "pcb",
        "trace",
        "--project",
        str(project),
        "--geometry",
        str(geometry),
        "--net",
        "GND",
        "--points",
        "10,10 20,10",
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    assert payload(bad)["error"]["code"] == "E_VALIDATION"
    assert payload(bad)["error"]["details"]["count"] >= 1
    good = run_cli(
        "pcb",
        "trace",
        "--project",
        str(project),
        "--geometry",
        str(geometry),
        "--file",
        str(plan),
        "--dry-run",
        config_dir=tmp_path / "config",
    )
    _assert_needs_native(good)
