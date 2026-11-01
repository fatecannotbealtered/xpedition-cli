"""Offline admission tests; synthetic replies are not native constraints evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_main():
    from test_driver_v2 import _worker_fixture

    from xpedition_cli import driver
    from xpedition_cli.cli import pcb
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_WIDTHS_PROCESS_FIXTURE"])
    calls = []
    patches = pytest.MonkeyPatch()

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            calls.append({"method": method, "apply": params.get("apply")})
            if method == "identity":
                return {
                    "application": {"pid": 4242, "active_project": str(folder / "owned.pcb")},
                    "adapter": _worker_fixture(),
                }
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "net_rules":
                # The old public plan accepted this plausible reply without eligibility.
                return {
                    "pcb": str(folder / "owned.pcb"),
                    "classes": {"POWER": {"nets": ["VBAT"], "widths": {}}},
                    "missing_nets": [],
                    "widths": {"typical": 0.5, "minimum": 0.4, "expansion": 0.6},
                    "applied": bool(params.get("apply")),
                }
            raise AssertionError("No other native operations are permitted: " + method)

    backend = Backend()
    patches.setattr(driver, "NativeBackend", lambda: backend)
    patches.setattr(pcb, "native", lambda: backend)
    try:
        return main()
    finally:
        (folder / "synthetic-widths-native-events.json").write_text(
            json.dumps(calls), encoding="utf-8"
        )
        patches.undo()


def run_process(folder, *args, success=True, synthetic=True):
    scope = folder / "process-scope.json"
    scope.write_text(
        json.dumps(
            {
                "scope_id": "owned-offline-widths-process",
                "tool": "xpedition-cli",
                "granted_by": "owned synthetic process fixture",
                "granted_at": "2026-10-17T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "session", "artifact", "change", "persist", "configure"],
                "dangerous": True,
                "roots": [str(folder)],
                "outputs": [str(folder)],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(folder / "config"),
        "XPEDITION_NATIVE_COMMAND": str(folder / "no-live-adapter.exe"),
        "XPEDITION_WIDTHS_PROCESS_FIXTURE": str(folder),
        "XPEDITION_CLI_SCOPE_FILE": str(scope),
    }
    bootstrap = (
        "import sys; "
        f"sys.path.insert(0, {str(ROOT / 'tests')!r}); "
        "from test_widths_safety import _synthetic_main; "
        "raise SystemExit(_synthetic_main())"
    )
    prefix = (
        [sys.executable, "-c", bootstrap] if synthetic else [sys.executable, "-m", "xpedition_cli"]
    )
    result = subprocess.run(
        [*prefix, *args, "--compact"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    body = json.loads(result.stdout)
    assert (result.returncode == 0) is success and body["ok"] is success, (body, result.stderr)
    return body["data"] if success else body["error"]


def widths_args(folder):
    (folder / "owned.pcb").write_bytes(b"owned offline fixture")
    return [
        "pcb",
        "constraints",
        "widths",
        "--project",
        str(folder / "owned.pcb"),
        "--class",
        "POWER",
        "--nets",
        "VBAT,+3V3",
        "--width",
        "0.5",
    ]


@pytest.mark.parametrize("dangerous", [False, True])
def test_public_widths_refuses_the_legacy_missing_eligibility_reply(tmp_path, dangerous):
    args = widths_args(tmp_path)
    if dangerous:
        args.append("--dangerous")
    planned = run_process(tmp_path, *args, "--dry-run")
    preview = planned["preview"]
    assert preview["can_apply"] is False
    legacy = preview["extensions"]["legacy_preview"]
    assert legacy["can_apply"] is False and legacy["native_state"] == "not_observed"
    assert legacy["eligibility"]["eligible"] is False
    assert [row["action"] for row in legacy["changes"]] == [
        "ensure_net_class",
        "assign_nets",
        "set_trace_widths",
        "synch_ces",
    ]
    assert legacy["widths"] == {"typical": 0.5, "minimum": 0.4, "expansion": 0.6}
    assert legacy["nets"] == ["VBAT", "+3V3"]
    assert {row["code"] for row in legacy["blockers"]} <= set(preview["unknowns"])
    assert set(legacy["unknowns"]) <= set(preview["unknowns"])
    assert any("all three" in value and "every layer" in value for value in preview["constraints"])
    assert json.loads((tmp_path / "synthetic-widths-native-events.json").read_text()) == []
    for _ in range(2):
        error = run_process(tmp_path, *args, "--confirm", planned["confirm_token"], success=False)
        assert error["code"] == "E_CONFLICT"
        assert error["details"]["dispatch_state"] == "not_started"
        assert json.loads((tmp_path / "synthetic-widths-native-events.json").read_text()) == []


@pytest.mark.parametrize("apply", [False, True])
def test_direct_net_rules_dispatch_refuses_before_com_import(monkeypatch, apply):
    from xpedition_cli import native_com_adapter as native

    monkeypatch.setattr(native, "_import_com", lambda: pytest.fail("COM import attempted"))
    with pytest.raises(native.AdapterError) as caught:
        native.dispatch("net_rules", {"apply": apply, "manual_verified": True})
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["write_attempted"] is False


@pytest.mark.parametrize("apply", [False, True])
def test_direct_net_rules_helper_refuses_before_attachment(monkeypatch, apply):
    from xpedition_cli import native_com_adapter as native

    monkeypatch.setattr(native, "_application", lambda *a, **k: pytest.fail("native attach"))
    monkeypatch.setattr(native, "_constraint_design", lambda *a, **k: pytest.fail("CES attach"))
    with pytest.raises(native.AdapterError) as caught:
        native._net_rules({"apply": apply, "manual_verified": True}, None)
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["write_attempted"] is False


def test_public_discovery_and_real_offline_preview_retain_widths_scope(tmp_path):
    from xpedition_cli.cli.registry import by_path

    command = by_path()["pcb constraints widths"]
    assert command.needs == "none" and command.writes
    reference = run_process(tmp_path, "reference", "--command", command.path, synthetic=False)[
        "command"
    ]
    assert "can_apply=false" in reference["description"]
    assert "all three" in reference["description"] and "every layer" in reference["description"]
    assert "single IConstraint.Value PUT" in reference["description"]
    assert "docs/CURRENT-CANDIDATE.md" in reference["description"]
    assert len(run_process(tmp_path, "reference", "--index", synthetic=False)["commands"]) == 65
    planned = run_process(tmp_path, *widths_args(tmp_path), "--dry-run", synthetic=False)
    assert planned["preview"]["can_apply"] is False
    help_result = subprocess.run(
        [sys.executable, "-m", "xpedition_cli", *command.path.split(), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert help_result.returncode == 0 and "can_apply=false" in help_result.stdout
    assert "all three" in help_result.stdout and "every layer" in help_result.stdout


def test_direct_widths_preview_retains_explicit_values_without_native_observation(
    tmp_path, monkeypatch
):
    from xpedition_cli.cli import pcb

    board = tmp_path / "owned.pcb"
    board.write_bytes(b"offline fixture")
    monkeypatch.setattr(pcb, "native", lambda: pytest.fail("no native calls"))
    preview = pcb.rules(
        {
            "project": str(board),
            "class": "POWER",
            "nets": "A,B",
            "width": 0.5,
            "min": 0.25,
            "expansion": 0.75,
            "dry_run": True,
        }
    )["preview"]
    widths = {"typical": 0.5, "minimum": 0.25, "expansion": 0.75}
    assert preview["widths"] == widths and preview["changes"][2]["widths"] == widths
    assert preview["classes"] is None and preview["missing_nets"] is None
    assert preview["native_state"] == "not_observed" and preview["can_apply"] is False


@pytest.mark.parametrize("confirm", [None, "not-a-token"])
def test_direct_widths_apply_refuses_without_native_or_token_consumption(
    tmp_path, monkeypatch, confirm
):
    from xpedition_cli.cli import pcb
    from xpedition_cli.errors import CLIError

    board = tmp_path / "owned.pcb"
    board.write_bytes(b"offline fixture")
    monkeypatch.setattr(pcb, "native", lambda: pytest.fail("no native calls"))
    monkeypatch.setattr(pcb, "confirmed", lambda *a: pytest.fail("no token consumption"))
    with pytest.raises(CLIError) as caught:
        pcb.rules({"project": str(board), "class": "P", "width": 0.5, "confirm": confirm})
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["write_attempted"] is False
    assert caught.value.details["native_state"] == "not_observed"


def test_other_rule_reads_still_dispatch_normally(monkeypatch):
    from types import SimpleNamespace

    from xpedition_cli import native_com_adapter as native
    from xpedition_cli.cli.registry import by_path

    assert by_path()["pcb inspect"].needs == "layout"
    events = []
    com = SimpleNamespace(
        CoInitialize=lambda: events.append("initialize"),
        CoUninitialize=lambda: events.append("uninitialize"),
    )
    monkeypatch.setattr(native, "_import_com", lambda: (com, None))
    monkeypatch.setattr(native, "_heal_gen_py_cache", lambda: None)
    monkeypatch.setattr(native, "_board_info", lambda params, client: {"rules": "fixture"})
    assert native.dispatch("board_info", {"rules": True}) == {"rules": "fixture"}
    assert events == ["initialize", "uninitialize"]


@pytest.mark.parametrize("dangerous", [False, True])
def test_legacy_eligible_session_token_refuses_before_confirmation_observation(
    tmp_path, monkeypatch, dangerous
):
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    args = widths_args(tmp_path)
    if dangerous:
        args.append("--dangerous")
    calls = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            calls.append(method)
            if method == "identity":
                return {
                    "application": {"pid": 4242, "active_project": str(tmp_path / "owned.pcb")},
                    "adapter": _worker_fixture(),
                }
            if method == "snapshot":
                return {"components": [], "fixture": True}
            raise AssertionError("No other native operations are permitted: " + method)

    monkeypatch.setattr(driver, "NativeBackend", Backend)

    class LegacyAdmissionAdapter(driver.Adapter):
        def plan(self, call):
            fields = super().plan(call)
            # Issue a genuine signed SDK token with the vulnerable earlier plan shape.
            # Only this owned temporary fixture admits it; production plan stays false.
            fields["can_apply"] = True
            fields["target"].update(mode="session", engine_id="xpedition-pcb", instance_id="4242")
            fields["target"]["extensions"].update(
                project=str(tmp_path / "owned.pcb"),
                application={"pid": 4242},
                worker_adapter=_worker_fixture(),
            )
            return fields

    state = tmp_path / "legacy-token-state"
    with driver.runtime(adapter=LegacyAdmissionAdapter(), state_dir=state) as cli:
        result = cli.run([*args, "--dry-run"])
    assert result.envelope["ok"], result.envelope
    planned = result.envelope["data"]
    plan = planned["preview"]
    assert plan["can_apply"] is True and plan["target"]["mode"] == "session"
    assert plan["target"]["extensions"]["command"] == "pcb constraints widths"
    assert calls == []
    for _ in range(2):
        with driver.runtime(state_dir=state) as cli:
            refused = cli.run([*args, "--confirm", planned["confirm_token"]])
            error = refused.envelope["error"]
            assert error["code"] == "E_UNSUPPORTED", (error, calls)
            assert error["details"]["dispatch_state"] == "not_started"
            assert error["details"]["write_attempted"] is False
            assert calls == []
            record = cli._store.get(plan["operation_id"])
            assert record["status"] == "planned" and record["started_at"] is None
            assert (
                cli._store.connection()
                .execute("SELECT COUNT(*) FROM consumed_tokens")
                .fetchone()[0]
                == 0
            )
