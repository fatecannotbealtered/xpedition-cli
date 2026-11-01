from __future__ import annotations

import copy
import json

import handler_harness as cli
import pytest
from test_placement import Driver, guarded_plan, observations, task

from xpedition_cli.cli import common
from xpedition_cli.errors import CLIError
from xpedition_cli.placement import execute_placement


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
    observation.write_text(
        json.dumps({"schema_version": "1.0", "components": observations()}), encoding="utf-8"
    )
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
            result = guarded_plan(params["request"], self.driver)
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
    return ("pcb", "placement", "move", "--project", str(project), "--file", str(file))


def test_batch_write_roundtrip_is_three_adapter_calls_not_per_part(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
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
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    code, preview, _ = invoke(capsys, *args, "--dry-run")
    assert code == 0
    if change == "position":
        fake.driver.rows[0]["x"] += 1
    elif change == "identity":
        fake.driver.rows[0]["object_id"] = "new-component"
    else:
        inputs[0].write_text(
            json.dumps(task({"op": "translate", "dx": 3, "dy": 0})), encoding="utf-8"
        )
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 6 and result["error"]["code"] == "E_CONFLICT"
    assert not fake.driver.calls and not any(call["apply"] for call in fake.calls)


@pytest.mark.parametrize(
    "mode", ["partial_failure", "save_failure", "restore_failure", "swallowed"]
)
def test_cli_reports_nonretryable_partial_failures(inputs, monkeypatch, capsys, mode):
    fake = Native(inputs[2], mode)
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and result["error"]["code"] == "E_PROJECT_INVALID"
    assert result["error"]["retryable"] is False
    assert len(result["error"]["details"]["report"]["results"]) == 3


def test_apply_timeout_is_unknown_not_permission_to_retry(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    fake.timeout = True
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and result["error"]["retryable"] is False
    assert result["error"]["details"]["outcome"] == "unknown"
    assert result["error"]["details"]["cause_code"] == "E_TIMEOUT"
    assert "private upstream" not in json.dumps(result)


def test_submit_retains_known_partial_report_lifecycle_and_structured_error(
    inputs, monkeypatch, capsys
):
    fake = Native(inputs[2])
    original = fake.invoke
    partial = {
        "outcome": "partial_failure",
        "write_attempted": True,
        "saved": False,
        "drc_restored": True,
        "lifecycle": {"save_count": 0, "close_count": 0, "open_count": 0},
        "verification": {
            "valid": False,
            "difference": {
                "path": "model.texts[uid=593].strokes[4][5][0]",
                "expected": 25.4673,
                "actual": 25.4672,
            },
        },
        "results": [],
    }

    def invoke_partial(method, params, **kwargs):
        if params["apply"]:
            raise CLIError(
                "E_VERIFY_INCOMPLETE",
                "known native material mismatch",
                {"report": partial, "native_stage": "full_board_protection"},
            )
        return original(method, params, **kwargs)

    fake.invoke = invoke_partial
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    details = result["error"]["details"]
    assert code == 2 and details["outcome"] == "partial_failure"
    assert details["report"] == partial
    assert details["cause_message"] == "known native material mismatch"
    assert details["cause_details"]["native_stage"] == "full_board_protection"
    assert details["retry_safe"] is False


def test_placement_retains_structured_native_failure_message_and_details():
    from xpedition_cli.native_com_adapter import AdapterError

    driver = Driver()
    bound = guarded_plan(task(), driver)

    def fail(target):
        raise AdapterError(
            "E_NATIVE_FAILURE",
            "known native refusal",
            {"member": "Place", "native_reply_received": False},
        )

    driver.move = fail
    report = execute_placement(task(), bound["state_digest"], driver)
    assert report["issues"][0]["message"] == "known native refusal"
    assert report["issues"][0]["details"] == {"member": "Place", "native_reply_received": False}
    assert report["lifecycle"] == {"save_count": 0, "close_count": 0, "open_count": 0}


def test_cli_does_not_trust_adapter_verification_boolean(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
    args = native_args(inputs)
    _, preview, _ = invoke(capsys, *args, "--dry-run")
    fake.bad_result = True
    code, result, _ = invoke(capsys, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 2 and result["error"]["details"]["stage"] == "response"
    retained = result["error"]["details"]["report"]
    assert (
        retained["lifecycle"]["save_count"]
        == retained["lifecycle"]["close_count"]
        == retained["lifecycle"]["open_count"]
        == 1
    )
    assert retained["saved"] is True and retained["drc_restored"] is True


def test_inconsistent_preview_cannot_issue_a_confirmation(inputs, monkeypatch, capsys):
    fake = Native(inputs[2])
    fake.bad_preview = True
    monkeypatch.setattr(common, "NativeBackend", lambda: fake)
    code, result, _ = invoke(capsys, *native_args(inputs), "--dry-run")
    assert code != 0 and result["error"]["code"] == "E_SERVER"
    assert not (inputs[2].parent / "config" / "confirm.secret").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("pcb", "placement", "move", "--file", "--dry-run"),
        ("pcb", "placement", "move", "--project", "b.prj", "--file=a", "--file=b", "--dry-run"),
        ("pcb", "placement", "move", "--project", "b.prj", "--all", "--dry-run"),
        ("pcb", "placement", "move", "--project", "b.prj", "--file=a", "--input=b", "--dry-run"),
    ],
)
def test_bad_options_fail_before_files_or_backend(inputs, monkeypatch, capsys, args):
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid options reached the backend")

    monkeypatch.setattr(common, "NativeBackend", unexpected)
    code, result, _ = invoke(capsys, *args)
    assert code == 2 and result["error"]["code"] == "E_USAGE"


def test_reference_publishes_the_task_schema(inputs, capsys):
    code, result, _ = invoke(capsys, "reference", "--command", "pcb placement move", "--compact")
    assert code == 0
    [command] = result["data"]["commands"]
    assert command["file_json_schema"]["additionalProperties"] is False
    assert command["dry_run_output_schema"] in result["data"]["schemas"]
