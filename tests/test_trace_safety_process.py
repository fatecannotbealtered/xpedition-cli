"""Fresh public processes: synthetic native refusal and real executable discovery.

No CAD server, native route, native Save failure or physical routing acceptance
is implied by these subprocess fixtures. Production has no eligible trace write.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _synthetic_main():
    from test_driver_v2 import _scope, _worker_fixture
    from test_trace_safety import native_case

    from xpedition_cli import driver
    from xpedition_cli import native_com_adapter as native
    from xpedition_cli.cli import pcb
    from xpedition_cli.errors import CLIError
    from xpedition_cli.main import main

    folder = Path(os.environ["XPEDITION_TRACE_PROCESS_FIXTURE"])
    patches = pytest.MonkeyPatch()
    _scope(folder, patches)
    mode = os.environ.get("XPEDITION_TRACE_PROCESS_CASE", "copper")
    app, _, _, events = native_case(folder, patches, copper=mode == "copper")
    calls = []

    class Backend:
        read_timeout_seconds = 30

        def invoke(self, method, params, **kwargs):
            calls.append({"method": method, "apply": params.get("apply")})
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            if method == "cell_refresh_state":
                assert params["strict"] and params["routing_objects"]
                return app.ActiveDocument.state
            if method == "hand_route":
                try:
                    return native._hand_route(params, None)
                except native.AdapterError as error:
                    raise CLIError(error.code, str(error), error.details) from error
            raise AssertionError("No other native operations are permitted: " + method)

    backend = Backend()
    patches.setattr(driver, "NativeBackend", lambda: backend)
    patches.setattr(pcb, "native", lambda: backend)
    try:
        return main()
    finally:
        (folder / "synthetic-trace-native-events.json").write_text(
            json.dumps({"events": events, "calls": calls}), encoding="utf-8"
        )
        patches.undo()


def run_process(folder, *args, case="copper", synthetic=True, success=True):
    scope = folder / "process-scope.json"
    scope.write_text(
        json.dumps(
            {
                "scope_id": "owned-offline-trace-process",
                "tool": "xpedition-cli",
                "granted_by": "owned synthetic process fixture",
                "granted_at": "2026-10-16T00:00:00Z",
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
        "XPEDITION_TRACE_PROCESS_FIXTURE": str(folder),
        "XPEDITION_TRACE_PROCESS_CASE": case,
        "XPEDITION_CLI_SCOPE_FILE": str(scope),
    }
    if synthetic:
        bootstrap = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'tests')!r}); "
            "from test_trace_safety_process import _synthetic_main; "
            "raise SystemExit(_synthetic_main())"
        )
        prefix = [sys.executable, "-c", bootstrap]
    else:
        exe = os.environ.get("XPEDITION_CLI_PROCESS_EXE")
        prefix = [exe] if exe else [sys.executable, "-m", "xpedition_cli"]
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


def test_public_discovery_declares_explicit_fields_and_current_blockers(tmp_path):
    reference = run_process(
        tmp_path, "reference", "--command", "pcb routing trace", synthetic=False
    )
    command = reference["command"]
    assert "can_apply=false" in command["description"]
    params = command["params"]
    assert {p["name"] for p in params if p.get("required")} >= {
        "project",
        "net",
        "layer",
        "width",
        "points",
    }
    assert "file" not in {p["name"] for p in params}
    index = run_process(tmp_path, "reference", "--index", synthetic=False)
    assert len(index["commands"]) == 65


@pytest.mark.parametrize("case", ["copper", "no_copper"])
def test_public_blocked_preview_repeated_confirm_has_zero_native_replay(tmp_path, case):
    args = [
        "pcb",
        "routing",
        "trace",
        "--project",
        str(tmp_path / "owned.pcb"),
        "--net",
        "A",
        "--layer",
        "1",
        "--width",
        "0.254",
        "--points",
        "0,0 5,0",
        "--dangerous",
    ]
    planned = run_process(tmp_path, *args, "--dry-run", case=case)
    preview = planned["preview"]
    assert preview["can_apply"] is False
    legacy = preview["extensions"]["legacy_preview"]
    assert legacy["can_apply"] is False
    plan = legacy["trace"]
    assert plan["can_apply"] is False and plan["unit"] == "mm"
    assert plan["endpoints"][0]["physical_contact_verified"] is False
    blockers = {r["code"] for r in plan["blockers"]}
    assert blockers <= set(preview["unknowns"])
    assert any("do not retry" in value for value in preview["constraints"])
    assert "local_rule_applicability_unproven" in blockers
    assert ("generated_copper_impact_unproven" in blockers) is (case == "copper")
    for _ in range(2):
        error = run_process(
            tmp_path, *args, "--confirm", planned["confirm_token"], case=case, success=False
        )
        assert error["code"] == "E_CONFLICT"
        events = json.loads((tmp_path / "synthetic-trace-native-events.json").read_text())
        assert events["events"] == []
        assert not any(r["method"] == "hand_route" and r["apply"] for r in events["calls"])


@pytest.mark.parametrize("verb", ["auto", "via", "apply"])
def test_public_disabled_broader_paths_refuse_without_native_calls(tmp_path, verb):
    more = ["--net", "A", "--at", "0,0"] if verb == "via" else []
    if verb == "auto":
        more = ["--dangerous"]
    if verb == "apply":
        path = tmp_path / "plan.json"
        path.write_text('{"items":[]}', encoding="utf-8")
        more = ["--file", str(path)]
    (tmp_path / "owned.pcb").write_bytes(b"owned synthetic fixture")
    error = run_process(
        tmp_path,
        "pcb",
        "routing",
        verb,
        "--project",
        str(tmp_path / "owned.pcb"),
        *more,
        "--dry-run",
        synthetic=False,
        success=False,
    )
    assert error["code"] == "E_UNSUPPORTED"
    assert error["details"]["write_attempted"] is False
