"""Creation cost commitments and shared deadlines; no CAD execution."""

import copy
import inspect
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_owner_client as O
from xpedition_cli import designer_source_binding
from xpedition_cli import driver as D
from xpedition_cli import project_create_budget as C
from xpedition_cli.cli import project as P
from xpedition_cli.errors import CLIError
from xpedition_cli.operation_budget import (
    OperationBudget,
    budget_catalog,
    native_call_remaining,
    operation_request,
    profile_for,
)


def source(files=610, size=132655280):
    return {
        "scheme": "setup-template-closure-v1",
        "closure_sha256": "a" * 64,
        "file_count": files,
        "total_bytes": size,
    }


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


def test_complete_measured_source_has_stable_240_second_cost_and_public_cap():
    cost = C.cost_for_source(source())
    assert cost["required_seconds"] == 240
    assert cost["native_hard_cap_seconds"] == 600
    assert C.require_cost(source(), copy.deepcopy(cost)) == cost
    profile = profile_for("project create")
    assert profile["native_call_seconds"]["clone_project"] == 600
    assert profile["total_seconds"]["confirm"] == 2700
    assert profile["stage_seconds"]["dispatch"] == 1200
    assert profile["confirmation_reserve_seconds"] == {"observe_after": 660, "verify": 120}
    assert budget_catalog()["command_profiles"]["project create"] == profile["id"]
    assert "clone_project" not in profile_for("other")["native_call_seconds"]
    assert "native_requested_upper_bound_methods" not in profile_for("pcb render")


@pytest.mark.parametrize(
    "field,value",
    [
        ("file_count", True),
        ("file_count", 0),
        ("file_count", 8193),
        ("total_bytes", -1),
        ("total_bytes", 536870913),
        ("closure_sha256", "wrong"),
    ],
)
def test_missing_or_invalid_source_envelope_is_not_a_budget_authority(field, value):
    invalid = source()
    invalid[field] = value
    with pytest.raises(CLIError):
        C.require_cost(invalid)


@pytest.mark.parametrize("change", ["cost", "files", "bytes", "source", "model"])
def test_changed_stable_commitment_is_rejected(change):
    promised = C.cost_for_source(source())
    if change == "cost":
        promised["required_seconds"] = 239
    elif change == "files":
        promised["source"]["file_count"] += 1
    elif change == "bytes":
        promised["source"]["total_bytes"] += 1
    elif change == "source":
        promised["source"]["closure_sha256"] = "b" * 64
    else:
        promised["model"]["fixed_step_seconds"] = 1
    with pytest.raises(CLIError) as caught:
        C.require_cost(source(), promised)
    assert caught.value.details["native_request_dispatched"] is False


def test_over_budget_is_refused_not_silently_clamped():
    large = source(8192, 512 * 1024 * 1024)
    assert C.cost_for_source(large)["required_seconds"] == 705
    with pytest.raises(CLIError) as caught:
        C.require_cost(large)
    assert caught.value.code == "E_UNSUPPORTED"
    assert caught.value.details["write_attempted"] is False
    assert caught.value.details["project_create_cost"]["required_seconds"] == 705


def test_requested_cost_stage_and_total_reserves_share_one_deadline():
    clock = Clock()
    budget = OperationBudget("project create", "confirm", clock=clock, quiet=True)
    deadline = budget.deadline
    with budget.stage("dispatch"):
        with budget.native_call("clone_project", 240) as selected:
            assert selected == 240
            clock.value += 159
    assert budget.deadline == deadline
    late_clock = Clock()
    late = OperationBudget("project create", "confirm", clock=late_clock, quiet=True)
    late_clock.value += 1800
    with late.stage("dispatch"):
        with late.native_call("clone_project", 240) as selected:
            assert selected == 120  # Remaining total900 minus after/verify780.
    assert late.deadline == deadline
    assert late.report()["native_calls"][0]["limit_seconds"] == 120


def test_first_failure_is_not_lost_when_final_verification_cannot_complete():
    budget = OperationBudget("project create", "confirm", quiet=True)
    with pytest.raises(CLIError):
        with budget.stage("dispatch"):
            with budget.native_call("clone_project", 240):
                raise CLIError("E_OUTCOME_UNKNOWN", "original owner observer expired")
    with pytest.raises(CLIError):
        with budget.stage("verify"):
            raise CLIError("E_OUTCOME_UNKNOWN", "independent verification incomplete")
    assert budget.report()["first_failed_stage"] == "dispatch"
    assert budget.report()["failed_stage"] == "verify"


@pytest.fixture
def handler(tmp_path, monkeypatch):
    template = tmp_path / "setup/template.prj"
    template.parent.mkdir()
    template.write_text("offline official setup fixture")
    target = tmp_path / "new/design.prj"
    calls, scopes = [], []
    monkeypatch.setattr(P, "check_gate", lambda *_args: None)
    monkeypatch.setattr(
        P.project_file,
        "template_state",
        lambda _path: {"creation_method": "native_new_project", "initialized": False, "designs": 0},
    )
    monkeypatch.setattr(P, "confirmed", lambda options, scope: scopes.append(copy.deepcopy(scope)))
    monkeypatch.setattr(P, "previewed", lambda preview, scope: {"preview": preview, "scope": scope})
    monkeypatch.setattr(
        P,
        "native",
        lambda: NS(
            invoke=lambda method, params, **kwargs: (
                calls.append((method, params, kwargs)) or {"created": True}
            )
        ),
    )
    options = {
        "template": str(template),
        "project": str(target),
        "_project_create_source": source(),
        "_project_create_cost": C.cost_for_source(source()),
    }
    return NS(options=options, calls=calls, scopes=scopes)


def test_handler_preview_and_confirm_use_the_same_committed_cost(handler):
    preview = P.create({**handler.options, "dry_run": True})
    assert preview["preview"]["project_create_cost"] == handler.options["_project_create_cost"]
    assert handler.calls == []
    assert P.create(handler.options)["created"] is True
    assert handler.scopes == [preview["scope"]]
    assert len(handler.calls) == 1 and handler.calls[0][0] == "clone_project"
    assert handler.calls[0][2]["timeout_seconds"] == 240


@pytest.mark.parametrize("mode", ["missing", "oversized"])
def test_handler_rejects_uncommitted_or_oversized_source_before_backend(handler, mode):
    options = copy.deepcopy(handler.options)
    if mode == "missing":
        options.pop("_project_create_cost")
    else:
        options["_project_create_source"] = source(8192, 512 * 1024 * 1024)
        options["_project_create_cost"] = C.cost_for_source(options["_project_create_source"])
    with pytest.raises(CLIError):
        P.create(options)
    assert handler.calls == [] and handler.scopes == []


def test_real_observer_binds_cost_to_the_fresh_complete_source(tmp_path, monkeypatch):
    adapter = D.Adapter(backend=NS())
    selected = source()
    monkeypatch.setattr(designer_source_binding, "setup_source_binding", lambda _path: selected)
    target = {
        "mode": "file",
        "extensions": {
            "command": "project create",
            "path": str(tmp_path / "not-created.prj"),
            "input_paths": [],
            "designer_setup_template": str(tmp_path / "setup.prj"),
            "project_create_cost": C.cost_for_source(selected),
        },
    }
    snapshot = adapter._observe(target)
    assert (
        snapshot["extensions"]["facts"]["project_create_cost"]
        == target["extensions"]["project_create_cost"]
    )
    selected["closure_sha256"] = "b" * 64
    with pytest.raises(CLIError) as caught:
        adapter._observe(target)
    assert caught.value.details["native_request_dispatched"] is False


def test_real_plan_pins_the_same_cost_in_target_snapshot_and_handler_preview(tmp_path, monkeypatch):
    adapter = D.Adapter(backend=NS())
    cost = C.cost_for_source(source())
    target = {
        "mode": "file",
        "engine_id": "files",
        "instance_id": None,
        "object_id": str(tmp_path / "new.prj"),
        "locator": (tmp_path / "new.prj").as_uri(),
        "extensions": {"designer_setup_template": str(tmp_path / "setup.prj")},
    }
    monkeypatch.setattr(adapter, "_target", lambda *_args: target)
    monkeypatch.setattr(
        adapter,
        "_observe",
        lambda selected: {
            "id": "before",
            "revision": "a" * 64,
            "extensions": {
                "facts": {"designer_setup_source": source(), "project_create_cost": cost}
            },
        },
    )
    monkeypatch.setattr(D, "config_dir", lambda: tmp_path)
    options = []
    monkeypatch.setattr(
        D,
        "_handler",
        lambda _command: (
            lambda supplied: (
                options.append(supplied)
                or {"preview": {"changes": []}, "confirm_token": "offline-token"}
            )
        ),
    )
    call = NS(
        command="project create",
        params={"project": str(tmp_path / "new.prj")},
        dangerous=False,
        operation_id="op-create-budget",
        effects=("session", "change", "persist"),
    )
    result = adapter.plan(call)
    assert result["target"]["extensions"]["project_create_cost"] == cost
    assert result["base_snapshot"]["extensions"]["facts"]["project_create_cost"] == cost
    assert options[0]["_project_create_cost"] == cost
    assert options[0]["_project_create_source"] == source()


def test_confirmation_with_no_signed_cost_refuses_before_a_new_observation(monkeypatch):
    adapter = D.Adapter(backend=NS())
    monkeypatch.setattr(adapter, "_observe", lambda *_args: pytest.fail("No native observation"))
    with pytest.raises(D.DriverError):
        adapter.revision(
            {
                "extensions": {
                    "command": "project create",
                    "designer_setup_template": "fixture",
                    "operation_budget_profile": profile_for("project create"),
                }
            }
        )


def pending_error(operation="op-create-budget"):
    return CLIError(
        "E_OUTCOME_UNKNOWN",
        "The original owner command has no observed terminal result; do not replay",
        {
            "sdk_operation_id": operation,
            "owner_command_id": "owner-command-" + "a" * 32,
            "owner_ref": {
                "protocol": "designer-owned-session-v1",
                "owner_id": "a" * 32,
                "owner_epoch_sha256": "b" * 64,
                "source_bundle_sha256": "c" * 64,
            },
            "request_replayed": False,
            "write_attempted": True,
            "native_request_dispatched": None,
        },
    )


@pytest.mark.parametrize(
    "change",
    [
        "operation",
        "command",
        "reference",
        "replayed",
        "terminal",
        "failure",
        "dispatched",
        "missing",
        "message",
        "readonly",
    ],
)
def test_pending_classification_does_not_swallow_unrelated_or_terminal_errors(change):
    error = pending_error()
    assert D._owner_terminal_not_observed(error, "op-create-budget")
    if change == "operation":
        error.details["sdk_operation_id"] = "op-other"
    elif change == "command":
        error.details["owner_command_id"] = "owner-command-other"
    elif change == "reference":
        error.details["owner_ref"] = {}
    elif change == "replayed":
        error.details["request_replayed"] = True
    elif change == "terminal":
        error.details["owner_command_terminal_phase"] = "command_failed"
    elif change == "failure":
        error.details["native_failure"] = {}
    elif change == "dispatched":
        error.details["native_request_dispatched"] = False
    elif change == "missing":
        error.details.pop("native_request_dispatched")
    elif change == "message":
        error.message = "other unknown"
    else:
        error.details["write_attempted"] = False
    assert not D._owner_terminal_not_observed(error, "op-create-budget")


def test_pending_original_owner_call_does_not_queue_independent_cad_readback(tmp_path, monkeypatch):
    adapter = D.Adapter(backend=NS())
    error = pending_error()
    original = copy.deepcopy(error.details)
    cost = C.cost_for_source(source())
    plan = {
        "target": {
            "mode": "file",
            "extensions": {"designer_setup_template": "fixture", "project_create_cost": cost},
        },
        "base_snapshot": {
            "id": "before",
            "extensions": {
                "facts": {"designer_setup_source": source(), "project_create_cost": cost}
            },
        },
    }
    folder = tmp_path / "handler-plans"
    folder.mkdir()
    (folder / "op-create-budget.json").write_text('{"token":"offline-only"}')
    monkeypatch.setattr(D, "config_dir", lambda: tmp_path)
    calls = []

    def handler(_options):
        calls.append("one-original-write")
        raise error

    monkeypatch.setattr(D, "_handler", lambda _command: handler)
    monkeypatch.setattr(
        adapter,
        "_observe",
        lambda *_args, **_kwargs: pytest.fail("No CAD read after original terminal was unobserved"),
    )
    call = NS(
        command="project create",
        params={},
        dangerous=False,
        operation_id="op-create-budget",
        effects=("change", "persist"),
    )
    with operation_request("project create", "confirm", quiet=True) as budget:
        with pytest.raises(D.DriverError) as caught:
            inspect.unwrap(D.Adapter.apply)(adapter, call, plan)
    assert calls == ["one-original-write"]
    assert caught.value.code == "E_OUTCOME_UNKNOWN"
    receipt = caught.value.receipt
    assert receipt["outcome"]["execution"] == "unknown"
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["handler_error"]["details"] == original
    assert receipt["extensions"]["readback_error"] is None
    assert receipt["extensions"]["independent_readback"] == {
        "status": "unobserved",
        "native_request_dispatched": False,
        "reason": "original_owner_command_terminal_unobserved",
    }
    assert error.details == original and budget.report()["first_failed_stage"] == "dispatch"


@pytest.mark.parametrize("ticket_seconds,service_seconds", [(241, 0), (200, 41)])
def test_native_allocation_exhausted_in_setup_never_publishes_owner_request(
    monkeypatch, ticket_seconds, service_seconds
):
    clock, submits = Clock(), []
    fake_owner = NS(submit_owner_adapter_command=lambda *_args, **_kwargs: submits.append(True))

    def ticket(_locator):
        clock.value += ticket_seconds
        return fake_owner, NS(), None, None

    def service(*_args):
        clock.value += service_seconds
        return {"phase": "public_dispatch_ready"}

    monkeypatch.setattr(O, "_ticket", ticket)
    monkeypatch.setattr(O, "_authorization", lambda _locator: ("op-budget", {}))
    monkeypatch.setattr(O, "_service_observation", service)
    monkeypatch.setattr(O, "_service_unavailable", lambda *_args, **_kwargs: False)
    with operation_request("project create", "confirm", quiet=True, clock=clock) as budget:
        with pytest.raises(CLIError) as caught:
            with budget.stage("dispatch"), budget.native_call("clone_project", 240):
                O.call_owner(NS(), "clone_project", {}, 240)
    assert caught.value.code == "E_TIMEOUT" and submits == []
    assert caught.value.details["owner_request_published"] is False
    assert caught.value.details["native_request_dispatched"] is False
    assert budget.report()["native_calls"][0]["status"] == "not_dispatched"


def test_ticket_and_authorization_cost_are_not_added_again_to_a_published_call_deadline(
    monkeypatch,
):
    clock, submits, reads = Clock(), [], []
    command = "owner-command-" + "d" * 32
    packet = {"command_id": command, "observations": []}
    services = [0]

    def ticket(_locator):
        clock.value += 30
        return fake_owner, NS(), None, None

    def service(*_args):
        if not services[0]:
            clock.value += 10
        services[0] += 1
        return {"phase": "public_dispatch_ready"}

    def submit(*_args, **_kwargs):
        submits.append(clock.value)
        return copy.deepcopy(packet)

    def observe(*_args, timeout_seconds):
        reads.append(clock.value)
        clock.value += timeout_seconds
        return copy.deepcopy(packet)

    fake_owner = NS(submit_owner_adapter_command=submit, observe_owner_command=observe)
    monkeypatch.setattr(O, "_ticket", ticket)
    monkeypatch.setattr(O, "_authorization", lambda _locator: ("op-budget", {}))
    monkeypatch.setattr(O, "_service_observation", service)
    monkeypatch.setattr(O, "_service_unavailable", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(O.time, "monotonic", clock)
    monkeypatch.setattr(
        O.time, "sleep", lambda seconds: setattr(clock, "value", clock.value + seconds)
    )
    locator = NS(reference=pending_error().details["owner_ref"])
    with operation_request("project create", "confirm", quiet=True, clock=clock) as budget:
        with pytest.raises(CLIError) as caught:
            with budget.stage("dispatch"), budget.native_call("clone_project", 240):
                assert native_call_remaining("clone_project") == 240
                O.call_owner(locator, "clone_project", {}, 240)
        assert native_call_remaining("clone_project") is None
    assert caught.value.code == "E_OUTCOME_UNKNOWN"
    assert caught.value.details["owner_command_id"] == command
    assert caught.value.details["request_replayed"] is False
    assert len(submits) == 1 and reads
    assert 240 <= clock.value <= 240.001  # Not the old now+240=280 reset after setup.
    assert budget.report()["native_calls"][0]["wall_ms"] == 240000


def test_remaining_helper_is_not_enabled_for_other_native_methods():
    clock = Clock()
    with operation_request("project create", "confirm", quiet=True, clock=clock) as budget:
        with budget.stage("dispatch"), budget.native_call("save", 30):
            assert native_call_remaining("save") is None
