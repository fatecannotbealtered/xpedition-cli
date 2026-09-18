from __future__ import annotations

import copy
import json

import pytest

from xpedition_cli import main as cli
from xpedition_cli import backends
from xpedition_cli.errors import CLIError
from xpedition_cli.placement import execute_placement, plan_placement
from test_placement import Driver, observations, task


def invoke(capsys, *args):
    code = cli.main(list(args))
    streams = capsys.readouterr()
    return code, json.loads(streams.out), streams.err


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "config"))
    file = tmp_path / "task.json"
    file.write_text(json.dumps(task()), encoding="utf-8")
    observation = tmp_path / "observations.json"
    observation.write_text(json.dumps({"schema_version": "1.0", "components": observations()}), encoding="utf-8")
    project = tmp_path / "board.prj"
    project.write_text("synthetic fixture, not an Xpedition project", encoding="utf-8")
    return file, observation, project


class Native:
    def __init__(self, project, mode="normal"):
        self.driver = Driver(mode)
        self.project = str(project)
        self.calls = []
        self.timeout = False
        self.bad_preview = False
        self.bad_result = False

    def require_implemented(self):
        pass

    def invoke(self, method, params, **kwargs):
        assert method == "placement_batch"
        self.calls.append(copy.deepcopy(params))
        if not params["apply"]:
            result = plan_placement(params["request"], self.driver.rows)
            if self.bad_preview:
                result["results"][0]["target"]["x"] += 1
        else:
            if self.timeout:
                raise CLIError("E_TIMEOUT", "private upstream failure")
            result = execute_placement(params["request"], params["state_digest"], self.driver)
            if self.bad_result:
                result["results"][0]["observed"]["x"] += 1
        result["pcb"], result["prompts"] = self.project, []
        return result


def native_args(inputs):
    file, _, project = inputs
    return ("pcb", "placement", "--backend", "native_xpedition", "--project", str(project), "--file", str(file))


def test_offline_plan_is_a_read_only_cli_command(inputs, monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        raise AssertionError("offline planner must not construct a native backend")
    monkeypatch.setattr(backends, "NativeBackend", unexpected)
    file, observation, project = inputs
    old = project.read_bytes()
    code, result, _ = invoke(capsys, "pcb", "placement-plan", "--file", str(file), "--input", str(observation), "--compact")
    assert code == 0 and result["ok"]
    assert result["data"]["summary"]["changed_count"] == 3
    assert result["data"]["validation"]["native_smoke"] == "missing"
    assert project.read_bytes() == old
    assert not (project.parent / "config" / "confirm.secret").exists()


def test_batch_write_roundtrip_is_three_adapter_calls_not_per_part(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    monkeypatch.setattr(backends, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    code, preview, _ = invoke(capsys, *args, "--dry-run")
    assert code == 0 and not fake.driver.calls
    token = preview["data"]["confirm_token"]
    code, result, _ = invoke(capsys, *args, "--confirm", token)
    assert code == 0 and result["data"]["verification"]["valid"]
    assert len(fake.calls) == 3
    assert [call["apply"] for call in fake.calls] == [False, False, True]
    assert fake.driver.calls.count("save") == 1
    code, result, _ = invoke(capsys, *args, "--confirm", token)
    assert code == 6 and result["error"]["code"] == "E_CONFLICT"
    assert sum(call["apply"] for call in fake.calls) == 1


@pytest.mark.parametrize("change", ["position", "identity", "file"])
def test_stale_preview_or_changed_task_never_executes(inputs, monkeypatch, capsys, change):
    fake = Native(inputs[2])
    monkeypatch.setattr(backends, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    code, preview, _ = invoke(capsys, *args, "--dry-run")
    assert code == 0
    if change == "position":
        fake.driver.rows[0]["x"] += 1
    elif change == "identity":
        fake.driver.rows[0]["object_id"] = "new-component"
    else:
        inputs[0].write_text(json.dumps(task({"op": "translate", "dx": 3, "dy": 0})), encoding="utf-8")
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 6 and result["error"]["code"] == "E_CONFLICT"
    assert not fake.driver.calls and not any(call["apply"] for call in fake.calls)


@pytest.mark.parametrize("mode", ["partial_failure", "save_failure", "restore_failure", "swallowed"])
def test_cli_reports_nonretryable_partial_failures(inputs, monkeypatch, capsys, mode):
    fake = Native(inputs[2], mode)
    monkeypatch.setattr(backends, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and result["error"]["code"] == "E_PROJECT_INVALID"
    assert result["error"]["retryable"] is False
    assert len(result["error"]["details"]["report"]["results"]) == 3


def test_apply_timeout_is_unknown_not_permission_to_retry(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    monkeypatch.setattr(backends, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    fake.timeout = True
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and result["error"]["retryable"] is False
    assert result["error"]["details"]["outcome"] == "unknown"
    assert result["error"]["details"]["cause_code"] == "E_TIMEOUT"
    assert "private upstream" not in json.dumps(result)


def test_cli_does_not_trust_adapter_verification_boolean(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    monkeypatch.setattr(backends, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    fake.bad_result = True
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and result["error"]["details"]["stage"] == "response"


def test_inconsistent_preview_cannot_issue_a_confirmation(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    fake.bad_preview = True
    monkeypatch.setattr(backends, "NativeBackend", lambda: fake)
    code, result, _ = invoke(capsys, *native_args(inputs), "--dry-run")
    assert code != 0 and result["error"]["code"] == "E_SERVER"
    assert not (inputs[2].parent / "config" / "confirm.secret").exists()


@pytest.mark.parametrize("args", [
    ("pcb", "placement", "--file", "--dry-run"),
    ("pcb", "placement", "--file=a", "--file=b", "--dry-run"),
    ("pcb", "placement", "--all", "--dry-run"),
    ("pcb", "placement-plan", "--confirm", "ct_invalid"),
    ("pcb", "placement-plan", "--file=a", "--input=b", "extra"),
    ("pcb", "placement-plan", "--file=a", "--input="),
])
def test_bad_options_fail_before_files_or_backend(inputs, monkeypatch, capsys, args):
    from xpedition_cli import placement_command
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid options reached input reading")
    monkeypatch.setattr(placement_command, "read_json", unexpected)
    code, result, _ = invoke(capsys, *args)
    assert code == 2 and result["error"]["code"] == "E_USAGE"


def test_reference_declares_input_output_safety_and_evidence(inputs, capsys):
    code, result, _ = invoke(capsys, "reference", "--compact")
    assert code == 0
    data = result["data"]
    commands = {c["path"]: c for c in data["commands"]}
    for path in ("pcb placement-plan", "pcb placement"):
        c = commands[path]
        assert c["output_schema"] in data["schemas"]
        assert c["input_json_schema"]["additionalProperties"] is False
        assert c["verification"]["native_smoke"] == "missing"
    assert commands["pcb placement"]["dry_run_output_schema"] in data["schemas"]
    for name in ("dry-run", "confirm"):
        assert "pcb placement" in next(f for f in data["global_flags"] if f["name"] == name)["applies_to"]


def test_release_readiness_is_beta_while_new_native_smoke_is_missing(inputs, capsys):
    code, result, _ = invoke(capsys, "reference", "--compact")
    assert code == 0
    readiness = result["data"]["release_readiness"]
    assert readiness["level"] == "beta" and readiness["live_smoke_status"] == "missing"
    assert "placement" in readiness["reason"]
    code, result, _ = invoke(capsys, "doctor", "--compact")
    assert code == 0
    check = next(c for c in result["data"]["checks"] if c["check"] == "release_readiness")
    assert "beta" in json.dumps(check)
