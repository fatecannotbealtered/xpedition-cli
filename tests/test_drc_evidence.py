"""CLI evidence gates against a simulated adapter; not native checker evidence."""

import copy
import subprocess

import pytest


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "fixture.pcb"
    path.write_text("offline fixture only", encoding="utf-8")
    return path


def completed_report(board):
    # A hypothetical adapter evidence record used to exercise the consumer.
    # The current UI adapter deliberately cannot produce this complete record.
    return {
        "pcb": str(board),
        "ran": True,
        "count": 0,
        "hazards": [],
        "errors": 0,
        "clean": True,
        "passes": True,
        "completion": {"status": "completed", "timed_out": False},
        "source": {
            "kind": "native_checker",
            "complete": True,
            "freshness": "current_run",
            "application_version": "fixture-app",
            "checker_version": "fixture-checker",
            "input": {
                "requested_project": str(board),
                "observed_pcb": str(board),
                "snapshot_sha256": "1" * 64,
            },
            "coverage": {
                "rule_config_sha256": "2" * 64,
                "rules_checked": ["fixture_clearance"],
                "excluded_rules": [],
                "rule_execution_verification": {"selected_rules_verified": True},
            },
        },
    }


def test_complete_current_evidence_can_be_consumed_without_promoting_native_support(
    cli, adapter, board
):
    adapter.on("batch_drc", completed_report(board))
    adapter.on("board_geometry", {"model": {}})
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    assert code == 0 and payload["data"]["verification"]["status"] == "passed"
    assert payload["data"]["passes"] is True and payload["data"]["clean"] is True


@pytest.mark.parametrize(
    "path,value",
    [
        (("completion",), None),
        (("completion", "status"), "incomplete"),
        (("completion", "timed_out"), True),
        (("ran",), False),
        (("source",), []),
        (("source", "complete"), 1),
        (("source", "freshness"), "existing"),
        (("source", "application_version"), None),
        (("source", "checker_version"), "unknown "),
        (("source", "input", "requested_project"), "another.pcb"),
        (("source", "input", "observed_pcb"), "another.pcb"),
        (("source", "input", "snapshot_sha256"), None),
        (("source", "coverage", "rule_config_sha256"), None),
        (("source", "coverage", "rules_checked"), []),
        (("source", "coverage", "excluded_rules"), None),
        (("source", "coverage", "rule_execution_verification"), None),
    ],
)
def test_incomplete_stale_or_unbound_zero_hazards_never_pass(cli, adapter, board, path, value):
    result = completed_report(board)
    container = result
    for name in path[:-1]:
        container = container[name]
    container[path[-1]] = value
    adapter.on("batch_drc", result)
    adapter.on("board_geometry", {"model": {}})
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    assert code == 0
    data = payload["data"]
    assert data["verification"]["status"] == "incomplete"
    assert data["passes"] is False and data["clean"] is False


def test_legacy_success_without_completion_evidence_is_not_a_pass(cli, adapter, board):
    adapter.on(
        "batch_drc", {"pcb": str(board), "ran": True, "count": 0, "clean": True, "passes": True}
    )
    adapter.on("board_geometry", {"model": {}})
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    assert code == 0 and payload["data"]["verification"]["status"] == "incomplete"
    assert payload["data"]["clean"] is False and payload["data"]["passes"] is False


def test_run_rejects_removed_no_run_flag_without_executing_checker(cli, adapter, board):
    adapter.on("batch_drc", completed_report(board))
    adapter.on("board_geometry", {"model": {}})
    code, payload = cli("pcb", "checks", "run", "--project", str(board), "--no-run")
    assert code == 2 and payload["error"]["code"] == "E_USAGE"
    assert adapter.calls == []


@pytest.mark.parametrize("child_timeout", [False, True])
def test_timeout_reports_unknown_without_reading_geometry_or_retrying(
    cli, adapter, board, child_timeout
):
    if child_timeout:

        def timeout(params):
            raise subprocess.TimeoutExpired("simulated adapter", 900)

        adapter.on("batch_drc", timeout)
    else:
        adapter.fail("batch_drc", "E_TIMEOUT")
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    error = payload["error"]
    assert code == 2 and error["code"] == "E_PROJECT_INVALID" and error["retryable"] is False
    assert error["details"]["outcome"] == "unknown"
    assert error["details"]["verification"]["status"] == "incomplete"
    assert adapter.methods().count("batch_drc") == 1 and "board_geometry" not in adapter.methods()


def test_complete_evidence_with_violations_is_failed_not_passed(cli, adapter, board):
    result = copy.deepcopy(completed_report(board))
    result.update(
        count=1, hazards=[{"kind": "fixture_clearance"}], errors=1, clean=False, passes=False
    )
    adapter.on("batch_drc", result)
    adapter.on("board_geometry", {"model": {}})
    code, payload = cli("pcb", "checks", "run", "--project", str(board))
    data = payload["data"]
    assert code == 0 and data["verification"]["evidence_complete"] is True
    assert data["verification"]["status"] == "failed" and data["passes"] is False
