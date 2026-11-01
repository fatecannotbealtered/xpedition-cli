"""Budget regression tests use fake clocks and never connect to CAD."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from xpedition_cli import operation_budget
from xpedition_cli.backends import native_xpedition
from xpedition_cli.errors import CLIError
from xpedition_cli.operation_budget import (
    OperationBudget,
    budget_catalog,
    current_budget,
    operation_request,
    operation_stage,
    profile_for,
)


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_fixed_profiles_have_finite_totals_stages_and_signed_observation_limits():
    catalog = budget_catalog()
    assert catalog["default_profile"] in catalog["profiles"]
    for profile in catalog["profiles"].values():
        assert all(0 < value <= 7560 for value in profile["total_seconds"].values())
        assert all(0 < value <= 4800 for value in profile["stage_seconds"].values())
        assert profile["native_call_seconds"]["snapshot"] == 300
        assert profile["native_call_seconds"]["board_geometry"] in {300, 600}
        assert (
            sum(profile["confirmation_reserve_seconds"].values())
            < profile["total_seconds"]["confirm"]
        )
    assert catalog["version"] == "native-budget-v2"
    assert profile_for("pcb render")["total_seconds"]["confirm"] == 2760
    assert profile_for("pcb view")["total_seconds"]["confirm"] == 1800
    assert profile_for("pcb inspect")["native_call_seconds"]["board_info"] == 1200
    assert profile_for("pcb placement move")["total_seconds"]["confirm"] == 4260
    assert profile_for("pcb routing trace")["total_seconds"]["confirm"] == 7560
    assert (
        profile_for("pcb routing trace")["native_phase_call_seconds"]["component_hand_route"][
            "apply"
        ]
        == 3600
    )
    assert profile_for("schematic build")["stage_seconds"]["dispatch"] == 1800
    first = profile_for("pcb render")
    first["native_call_seconds"]["snapshot"] = 1
    assert profile_for("pcb render")["native_call_seconds"]["snapshot"] == 300


def test_one_monotonic_total_covers_confirm_observation_dispatch_and_readback():
    clock, events = Clock(), []
    with operation_request("pcb render", "confirm", clock=clock, progress=events.append) as budget:
        with operation_stage("observe_confirm"):
            with budget.native_call("board_geometry", 60) as seconds:
                assert seconds == 600  # One complete read fits the bounded v2 allowance.
                clock.advance(174)
        with operation_request("pcb render", "confirm") as reused:
            assert reused is budget
            with operation_stage("dispatch"):
                budget.dispatched = True
                with budget.native_call("render_board", 600) as seconds:
                    assert seconds == 600
                    clock.advance(180)
        with operation_stage("observe_after"):
            with budget.native_call("board_geometry", 60) as seconds:
                assert seconds == 600
                clock.advance(174)
        report = budget.report()
    assert report["wall_ms"] == 528000
    assert report["native_call_count"] == 3
    assert report["cumulative_native_ms"] == 528000
    assert [item["stage"] for item in report["stages"]] == [
        "observe_confirm",
        "dispatch",
        "observe_after",
    ]
    assert [item["event"] for item in events].count("native_call_started") == 3
    assert current_budget() is None


def test_wall_time_includes_non_native_work_and_cumulative_call_time_does_not():
    clock = Clock()
    budget = OperationBudget("pcb render", "preview", clock=clock, quiet=True)
    with budget.stage("observe_plan"):
        with budget.native_call("snapshot", 120):
            clock.advance(24)
        clock.advance(6)
        with budget.native_call("board_geometry", 60):
            clock.advance(174)
    assert budget.report()["wall_ms"] == 204000
    assert budget.report()["cumulative_native_ms"] == 198000


def test_confirmation_dispatch_reserves_independent_readback_and_verification():
    clock = Clock()
    budget = OperationBudget("pcb render", "confirm", clock=clock, quiet=True)
    clock.advance(1100)
    with budget.stage("dispatch"):
        with budget.native_call("render_board", 600) as seconds:
            assert seconds == 580  # 2760 total - 1100 used - 960 readback - 120 verify.
            clock.advance(500)
    with budget.stage("observe_after"):
        with budget.native_call("board_geometry", 60) as seconds:
            assert seconds == 600
            clock.advance(174)
    with budget.stage("verify"):
        clock.advance(2)
    assert budget.report()["wall_ms"] == 1776000


@pytest.mark.parametrize(
    "phase,dispatched",
    [("observe_plan", False), ("observe_confirm", False), ("observe_after", True)],
)
def test_stage_timeout_reports_exact_phase_without_replaying(phase, dispatched):
    clock = Clock()
    budget = OperationBudget("pcb render", "confirm", clock=clock, quiet=True)
    budget.dispatched = dispatched
    with pytest.raises(CLIError) as raised:
        with budget.stage(phase):
            clock.advance(961)
            budget.checkpoint()
    assert raised.value.code == "E_TIMEOUT"
    assert raised.value.details["write_attempted"] is dispatched
    assert raised.value.details["failed_stage"] == phase
    assert budget.report()["native_call_count"] == 0
    assert budget.report()["stages"][0]["status"] == "failed"


def test_no_reserve_is_left_means_no_dispatch_stage_or_native_call():
    clock = Clock()
    budget = OperationBudget("pcb render", "confirm", clock=clock, quiet=True)
    clock.advance(1680)
    called = False
    with pytest.raises(CLIError) as raised:
        with budget.stage("dispatch"):
            called = True
    assert not called
    assert raised.value.details["write_attempted"] is False
    assert budget.report()["native_call_count"] == 0


def test_nested_os_wait_uses_phase_and_total_remaining_seconds():
    clock = Clock()
    budget = OperationBudget("session close", "confirm", clock=clock, quiet=True)
    assert budget.remaining_seconds() == 1500
    with budget.stage("observe_after"):
        assert budget.remaining_seconds() == 660
        clock.advance(12)
        assert budget.remaining_seconds() == 648
    clock.advance(800)
    with budget.stage("observe_after"):
        # The post-read stage reserves verification time from the same total.
        assert budget.remaining_seconds() == 568


def test_read_only_user_timeout_is_not_silently_raised():
    clock = Clock()
    budget = OperationBudget("schematic parts", "query", clock=clock, quiet=True)
    with budget.stage("query"):
        with budget.native_call("snapshot", 17.5) as seconds:
            assert seconds == 17.5


@pytest.mark.parametrize("requested", [0, -1, float("nan"), float("inf")])
def test_invalid_native_timeouts_are_rejected_before_a_call(requested):
    budget = OperationBudget("pcb render", "preview", quiet=True)
    with pytest.raises(CLIError) as raised:
        with budget.native_call("snapshot", requested):
            pytest.fail("invalid deadline dispatched a call")
    assert raised.value.code == "E_VALIDATION"
    assert budget.report()["native_call_count"] == 0


def test_exception_restores_outer_request_and_counts_one_failed_native_call():
    clock = Clock()
    with operation_request("pcb render", "confirm", clock=clock, quiet=True) as budget:
        with pytest.raises(RuntimeError):
            with operation_stage("dispatch"):
                with budget.native_call("render_board", 600):
                    clock.advance(2)
                    raise RuntimeError("offline fixture")
        assert current_budget() is budget
        assert budget.report()["native_calls"] == [
            {
                "method": "render_board",
                "stage": "dispatch",
                "limit_seconds": 600.0,
                "wall_ms": 2000.0,
                "status": "failed",
            }
        ]
    assert current_budget() is None


@pytest.fixture
def backend_fixture(tmp_path, monkeypatch):
    binary = tmp_path / "fixture-adapter.exe"
    binary.write_bytes(b"not a real executable")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(binary))
    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XPEDITION_EXPECTED_PID", "4242")
    monkeypatch.setenv("XPEDITION_REQUIRE_EXISTING", "1")
    monkeypatch.setenv("XPEDITION_OBSERVE_ONLY", "1")
    return monkeypatch


def _bound_params():
    return {"project": "owned.pcb", "observe_only": True, "start": False, "pid": 4242}


def test_pdf_child_budget_is_lower_than_parent_and_never_mutates_source_params(backend_fixture):
    requests = []
    params = {"project": "owned.prj", "output": "owned.pdf"}

    def returned(argv, **kwargs):
        requests.append(json.loads(kwargs["input"]))
        assert kwargs["timeout"] == 600
        assert kwargs["stderr"] is subprocess.PIPE
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"data":{"exported":true}}', "")

    backend_fixture.setattr(native_xpedition.subprocess, "run", returned)
    with operation_request("schematic export", "confirm", quiet=True) as budget:
        with operation_stage("dispatch"):
            native_xpedition.NativeBackend().invoke("export_pdf", params, timeout_seconds=600)
        assert budget.report()["native_call_count"] == 1
    assert requests[0]["params"]["_native_process_timeout_seconds"] == 585
    assert (
        requests[0]["params"]["_native_budget_profile_id"] == profile_for("schematic export")["id"]
    )
    assert params == {"project": "owned.prj", "output": "owned.pdf"}


def test_pdf_refuses_before_native_dispatch_when_only_return_reserve_remains(backend_fixture):
    backend_fixture.setattr(
        native_xpedition.subprocess, "run", lambda *a, **k: pytest.fail("No PDF request may start")
    )
    clock = Clock()
    with operation_request("schematic export", "confirm", quiet=True, clock=clock) as budget:
        with operation_stage("dispatch"):
            clock.advance(budget.profile["stage_seconds"]["dispatch"] - 10)
            with pytest.raises(CLIError) as raised:
                native_xpedition.NativeBackend().invoke(
                    "export_pdf", {"project": "owned.prj"}, timeout_seconds=600
                )
        assert raised.value.details["write_attempted"] is False
        assert raised.value.details["native_request_dispatched"] is False
        assert budget.report()["native_calls"][0]["status"] == "not_dispatched"


@pytest.mark.parametrize("method", ["snapshot", "board_geometry"])
def test_bound_observer_timeout_preserves_session_and_original_write_guard(backend_fixture, method):
    from xpedition_cli import session

    session.record_native_timeout("placement_batch")
    before = copy.deepcopy(session.read_state())
    calls, clock = [], Clock()

    def timed_out(argv, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        expected_limit = 300 if method == "snapshot" else 600
        assert kwargs["timeout"] == expected_limit
        clock.advance(expected_limit)
        raise subprocess.TimeoutExpired(argv, expected_limit)

    backend_fixture.setattr(native_xpedition.subprocess, "run", timed_out)
    with operation_request("pcb render", "confirm", clock=clock, quiet=True) as budget:
        with pytest.raises(CLIError) as raised:
            with operation_stage("observe_confirm"):
                native_xpedition.NativeBackend().invoke(method, _bound_params(), timeout_seconds=60)
        assert budget.report()["native_call_count"] == 1
    assert raised.value.details["native_query_only"] is True
    assert raised.value.details["write_attempted"] is False
    assert session.read_state() == before
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"observe_only": False},
        {"start": True},
        {"pid": 4243},
        {"pid": True},
        {"project": ""},
        {"output": "new.json"},
    ],
)
@pytest.mark.parametrize("method", ["snapshot", "board_geometry"])
def test_method_name_does_not_allow_unbound_reads_against_stale_write(
    backend_fixture, method, change
):
    from xpedition_cli import session

    session.record_native_timeout("placement_batch")
    backend_fixture.setattr(
        native_xpedition.subprocess, "run", lambda *a, **k: pytest.fail("unsafe read dispatched")
    )
    with pytest.raises(CLIError) as raised:
        native_xpedition.NativeBackend().invoke(method, {**_bound_params(), **change})
    assert raised.value.code == "E_CONFLICT"


@pytest.mark.parametrize("method", ["snapshot", "board_geometry"])
def test_unbound_observation_timeout_remains_stale(backend_fixture, method):
    from xpedition_cli import session

    def timed_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    backend_fixture.setattr(native_xpedition.subprocess, "run", timed_out)
    with pytest.raises(CLIError) as raised:
        native_xpedition.NativeBackend().invoke(method, {"project": "owned.pcb", "start": True})
    assert raised.value.details["native_query_only"] is False
    assert session.read_state()["timed_out_method"] == method


def test_public_render_preview_uses_profile_for_both_native_observers(tmp_path, monkeypatch):
    from test_driver_v2 import _scope, _worker_fixture

    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    project = tmp_path / "representative.pcb"
    project.write_bytes(b"offline fixture, not a CAD document")
    observed = []

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            observed.append((method, dict(params), kwargs["timeout_seconds"]))
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"pcb": {"components": [], "tracks": []}, "fixture": True}
            assert method == "board_geometry"
            return {"pcb": str(project), "model": {"fixture": True}}

    monkeypatch.setattr(
        driver,
        "_handler",
        lambda command: lambda options: {"confirm_token": "fixture-token", "preview": {}},
    )
    with driver.runtime(driver.Adapter(NativeFixture()), state_dir=tmp_path / "operations") as cli:
        reply = cli.run(
            [
                "pcb",
                "render",
                "--project",
                str(project),
                "--output",
                str(tmp_path / "board.png"),
                "--dry-run",
                "--quiet",
            ]
        )
    assert reply.envelope["ok"], reply.envelope
    preview = reply.envelope["data"]["preview"]
    assert preview["target"]["extensions"]["operation_budget_profile"] == profile_for("pcb render")
    assert preview["target"]["extensions"]["source_operation_id"] == preview["operation_id"]
    facts = preview["base_snapshot"]["extensions"]["facts"]
    assert "operation_budget" not in facts
    assert (
        preview["extensions"]["operation_budget"]["profile_id"] == profile_for("pcb render")["id"]
    )
    snapshot = next(item for item in observed if item[0] == "snapshot")
    geometry = next(item for item in observed if item[0] == "board_geometry")
    assert snapshot[2] == 300 and geometry[2] == 600
    for _method, params, _seconds in (snapshot, geometry):
        assert params["observe_only"] is True and params["start"] is False
        assert params["pid"] == 4242


def test_old_or_changed_budget_profile_refuses_before_native_or_handler():
    from xpedition_cli.driver import Adapter, DriverError

    class NoNativeCalls:
        def invoke(self, *args, **kwargs):
            pytest.fail("a changed profile observed native state")

    target = {
        "extensions": {
            "command": "pcb render",
            "source_operation_id": "op-fixture",
            "operation_budget_profile": profile_for("pcb render"),
        }
    }
    target["extensions"]["operation_budget_profile"]["native_call_seconds"]["board_geometry"] = 601
    with pytest.raises(DriverError) as raised:
        Adapter(NoNativeCalls()).revision(target)
    assert raised.value.code == "E_CONFLICT"
    assert raised.value.dispatch_state == "not_started"
    assert raised.value.details["operation_id"] == "op-fixture"
    assert raised.value.details["lookup_argv"] == ["operation", "show", "--id", "op-fixture"]


def test_inline_flags_choose_the_same_signed_budget_and_quiet_mode(tmp_path, monkeypatch, capsys):
    from test_driver_v2 import _scope

    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    modes = []
    original_handler = driver._handler

    def selected_handler(declaration):
        def handler(options):
            modes.append((current_budget().mode, current_budget().quiet))
            return original_handler(declaration)(options)

        return handler

    monkeypatch.setattr(driver, "_handler", selected_handler)
    args = [
        "kb",
        "bind",
        "--name",
        "fixture",
        "--url",
        "https://example.invalid/public",
        "--about",
        "metadata only",
        "--dangerous",
    ]
    with driver.runtime(state_dir=tmp_path / "operations") as cli:
        preview = cli.run([*args, "--dry-run=true", "--quiet=true"])
        assert preview.envelope["ok"], preview.envelope
        done = cli.run(
            [
                *args,
                "--dry-run=false",
                "--confirm=" + preview.envelope["data"]["confirm_token"],
                "--quiet=true",
            ]
        )
    assert modes == [("preview", True), ("confirm", True)]
    assert done.envelope["ok"], done.envelope
    assert done.envelope["data"]["extensions"]["operation_budget"]["mode"] == "confirm"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("failure_phase", ["observe_confirm", "observe_after", "dispatch"])
def test_public_timeout_retains_original_id_and_never_replays_write(
    tmp_path, monkeypatch, failure_phase
):
    from test_driver_v2 import _scope

    from xpedition_cli import driver
    from xpedition_cli.cli import project as project_cli

    _scope(tmp_path, monkeypatch)
    clock, writes, reads = Clock(), [], []
    project = tmp_path / "owned/renamed-example.prj"
    project.parent.mkdir()
    project.write_bytes(b"offline closed fixture")
    output = tmp_path / "once.zip"
    monkeypatch.setattr(operation_budget.time, "monotonic", clock)
    release = {
        "read_only": True,
        "save_count": 0,
        "close_count": 0,
        "release_preflight": {name: {} for name in ("library", "schematic", "pcb")},
    }
    monkeypatch.setattr(project_cli, "release_preflight", lambda source: release)

    def observe(self, target, *, after_dispatch=False):
        budget = current_budget()
        phase = budget.active_stage["stage"]
        reads.append(phase)
        if phase == failure_phase:
            clock.advance(budget.active_stage["limit_seconds"] + 1)
            budget.checkpoint()
        return {
            "id": "snapshot-fixture",
            "source": "disk",
            "revision": "unchanged-fixture",
            "observed_at": "2026-10-24T00:00:00Z",
            "dependencies": [],
            "extensions": {"facts": {"inputs": [], "release_preflight": release}},
        }

    def handler(options):
        if options.get("dry_run"):
            return {"confirm_token": "fixture-token", "preview": {}}
        writes.append("write-once")
        output.write_bytes(b"bounded synthetic result")
        if failure_phase == "dispatch":
            raise CLIError("E_TIMEOUT", "fixture adapter lost dispatch reply", {})
        return {"output": str(output), "applied": True, "saved": True, "persistence_verified": True}

    monkeypatch.setattr(driver.Adapter, "_observe", observe)
    monkeypatch.setattr(driver, "_handler", lambda command: handler)
    args = ["project", "backup", "--project", str(project), "--output", str(output)]
    with driver.runtime(state_dir=tmp_path / "operations") as cli:
        planned = cli.run([*args, "--dry-run", "--quiet"])
        assert planned.envelope["ok"], planned.envelope
        token = planned.envelope["data"]["confirm_token"]
        original_id = planned.envelope["data"]["preview"]["operation_id"]
        reply = cli.run([*args, "--confirm", token, "--quiet"])
        assert not reply.envelope["ok"], reply.envelope
        details = reply.envelope["error"]["details"]
        assert details["operation_id"] == original_id
        report = details["operation_budget"]
        if failure_phase == "observe_confirm":
            assert reply.envelope["error"]["code"] == "E_TIMEOUT"
            assert details["dispatch_state"] == "not_started"
            assert writes == []
            assert report["dispatch_started"] is False
        else:
            assert details["dispatch_state"] == "dispatched"
            assert writes == ["write-once"]
            receipt = details["receipt"]
            assert receipt["outcome"]["verification"]["status"] == "incomplete"
            assert receipt["extensions"]["operation_budget"]["dispatch_started"] is True
            if failure_phase == "observe_after":
                assert details["native_handler_returned"] is True
                assert receipt["outcome"]["execution"] == "completed"
                assert receipt["extensions"]["result"]["saved"] is True
            original_history = cli.run(["operation", "show", "--id", original_id]).envelope
            replay = cli.run([*args, "--confirm", token, "--quiet"])
            later_history = cli.run(["operation", "show", "--id", original_id]).envelope
            assert not replay.envelope["ok"]
            assert writes == ["write-once"]
            assert (
                later_history["data"]["operation_status"]["receipt"]
                == original_history["data"]["operation_status"]["receipt"]
            )


@pytest.mark.parametrize("no_op", [False, True])
def test_final_budget_report_never_changes_sealed_plan_or_receipt(tmp_path, monkeypatch, no_op):
    from test_component_connect_sdk import sdk_case

    from xpedition_cli import driver

    setup = sdk_case(tmp_path, monkeypatch, no_op=no_op)
    with driver.runtime() as cli:
        planned = cli.run([*setup.args, "--dry-run", "--quiet"])
        assert planned.envelope["ok"], planned.envelope
        preview = json.loads(planned.stdout)["data"]["preview"]
        original_plan = copy.deepcopy(cli._store.get(preview["operation_id"])["plan"])
        assert preview == original_plan
        done = cli.run(
            [*setup.args, "--confirm", planned.envelope["data"]["confirm_token"], "--quiet"]
        )
        assert done.envelope["ok"], done.envelope
        receipt = copy.deepcopy(done.envelope["data"]["receipt"])
        stored = copy.deepcopy(cli._store.get(receipt["operation_id"])["receipt"])
        assert receipt == stored
        assert cli._store.get(receipt["operation_id"])["plan"] == original_plan
        assert "operation_budget" in receipt["extensions"]
        assert "operation_budget" in done.envelope["data"]["extensions"]
        assert (
            done.envelope["data"]["extensions"]["operation_budget"]["wall_ms"]
            >= receipt["extensions"]["operation_budget"]["wall_ms"]
        )
        observed = cli.run(["operation", "show", "--id", receipt["operation_id"]])
        assert observed.envelope["data"]["operation_status"]["receipt"] == receipt


def test_second_component_connection_uses_full_unchanged_budget_bearing_receipt(
    tmp_path, monkeypatch
):
    from test_component_connect_sdk import prepare_continuation, saved_sdk_case

    from xpedition_cli import driver

    setup, first = saved_sdk_case(tmp_path, monkeypatch)
    first_receipt = copy.deepcopy(first["data"]["receipt"])
    args = prepare_continuation(setup, first)
    # The original fixture models one PutTrace and always allocates the same
    # synthetic UID. Model a second native allocation, retaining the first UID
    # and every original raw object; the real protection verifier stays active.
    doc = setup.case.adapter._licensed_document(None)
    original_put = doc.PutTrace

    def second_put(*values):
        returned = original_put(*values)
        doc.Traces[-1].UniqueId = "second-native-allocation"
        setup.case.state["routing_objects"][-1]["uid"] = "second-native-allocation"
        setup.case.state["routing_objects"][-1]["geometry"]["uid"] = "second-native-allocation"
        returned.UniqueId = "second-native-allocation"
        return returned

    doc.PutTrace = second_put
    with driver.runtime() as cli:
        preview = cli.run([*args, "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        complete = cli.run(
            [*args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"]
        )
        assert complete.envelope["ok"], complete.envelope
        second_receipt = complete.envelope["data"]["receipt"]
        assert second_receipt["operation_id"] != first_receipt["operation_id"]
        assert second_receipt["outcome"]["persistence"] == "saved"
        assert second_receipt["outcome"]["verification"]["status"] == "passed"
        assert cli._store.get(second_receipt["operation_id"])["receipt"] == second_receipt
        assert cli._store.get(first_receipt["operation_id"])["receipt"] == first_receipt
