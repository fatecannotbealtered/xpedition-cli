"""Synthetic costs/native fixtures test resource refusal; no 467-pin CAD qualification."""

from __future__ import annotations

import pytest
from test_board_component_connect import fixture_case
from test_component_connect_sdk import sdk_case

from xpedition_cli import board_component_connect as B
from xpedition_cli import component_execution_budget as E
from xpedition_cli import driver


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _timed(monkeypatch, costs, *, raw_cost=None):
    clock = Clock()
    monkeypatch.setattr(B, "time", clock)
    original, raw = B._observe, B._observe_before_transaction
    consumed = []

    def observed(*args):
        duration = costs[min(len(consumed), len(costs) - 1)]
        consumed.append(duration)
        clock.sleep(duration)
        return original(*args)

    def before_transaction(*args):
        clock.sleep(raw_cost if raw_cost is not None else costs[-1])
        return raw(*args)

    monkeypatch.setattr(B, "_observe", observed)
    monkeypatch.setattr(B, "_observe_before_transaction", before_transaction)
    return clock, consumed


def test_shared_engine_bound_and_complete_observation_cost_are_explicit():
    report = E.estimate(434.625, 6, E.ENGINE_SECONDS, phase="preview")
    assert report["native_apply_seconds"] == E.ENGINE_SECONDS == 3600
    assert report["remaining_reads"] == 6 and report["blocked"] is False
    assert report["estimated_seconds"] == 2727.75
    assert "material" in report["sample_unit"]
    assert report["phase_allowance_seconds"] == 120
    assert report["engine_planning_overhead_seconds"] == 900
    assert E.validate_report(report) == report


def test_non_read_spend_is_not_reserved_twice():
    report = E.estimate(500, 3, 1800, phase="before_transaction", spent_non_read_seconds=100)
    assert report["phase_allowance_seconds"] == 20
    assert report["estimated_seconds"] == 1520
    assert report["blocked"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "434"])
def test_invalid_measurement_cannot_become_budget_authority(bad):
    with pytest.raises(ValueError):
        E.estimate(bad, 6, E.ENGINE_SECONDS, phase="preview")


def test_cost_metadata_never_changes_engineering_plan_fingerprint(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _clock, _reads = _timed(monkeypatch, [1, 700])
    fast = B.execute(case.params, None, case.adapter)
    slow = B.execute(case.params, None, case.adapter)
    assert fast["trace"]["fingerprint"] == slow["trace"]["fingerprint"]
    assert fast["execution_resources"]["blocked"] is False
    assert slow["execution_resources"]["blocked"] is True
    assert slow["execution_resources"]["estimated_seconds"] > E.ENGINE_SECONDS
    assert "execution_resources" not in slow["trace"]
    assert B._fingerprint(slow["trace"]) == slow["trace"]["fingerprint"]
    assert case.events == []


def test_initial_slow_observation_refuses_before_transaction_and_ignores_overrides(
    tmp_path, monkeypatch
):
    case = fixture_case(tmp_path, monkeypatch)
    _clock, reads = _timed(monkeypatch, [1, 700])
    preview = B.execute(case.params, None, case.adapter)
    params = {
        **case.params,
        "apply": True,
        "expected": preview["trace"]["fingerprint"],
        "native_apply_seconds": 999999,
        "sampled_seconds": 0,
        "_execution_resources": {"blocked": False, "available_seconds": 999999},
    }
    with pytest.raises(case.adapter.AdapterError) as raised:
        B.execute(params, None, case.adapter)
    assert raised.value.code == "E_UNSUPPORTED"
    assert raised.value.details["write_attempted"] is False
    report = raised.value.details["resource_budget"]
    assert report["sampled_seconds"] == 700 and report["remaining_reads"] == 5
    assert report["available_seconds"] == 2900
    assert report["estimated_seconds"] > report["available_seconds"]
    assert (
        raised.value.details["native_result"]["trace"]["fingerprint"]
        == preview["trace"]["fingerprint"]
    )
    assert reads == [1, 700] and case.events == []


def test_third_read_slowdown_keeps_identity_checks_and_false_write_flag(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _clock, reads = _timed(monkeypatch, [1, 1, 700], raw_cost=700)
    preview = B.execute(case.params, None, case.adapter)
    with pytest.raises(case.adapter.AdapterError) as raised:
        B.execute(
            {**case.params, "apply": True, "expected": preview["trace"]["fingerprint"]},
            None,
            case.adapter,
        )
    assert raised.value.details["write_attempted"] is False
    result = raised.value.details["native_result"]
    assert "before_transaction_identity" in result
    assert "before_transaction_inputs" in result
    assert "post_write_error" not in result
    report = result["execution_resources"]
    assert report["phase"] == "before_transaction" and report["remaining_reads"] == 3
    assert report["blocked"] is True
    assert reads == [1, 1, 700] and case.events == []


def test_fast_fixture_keeps_full_transaction_save_close_reopen(tmp_path, monkeypatch):
    case = fixture_case(tmp_path, monkeypatch)
    _timed(monkeypatch, [1])
    preview = B.execute(case.params, None, case.adapter)
    result = B.execute(
        {**case.params, "apply": True, "expected": preview["trace"]["fingerprint"]},
        None,
        case.adapter,
    )
    assert result["persistence_verified"] is True
    assert result["execution_resources"]["blocked"] is False
    assert result["execution_resources"]["phase"] == "before_transaction"
    assert [item[0] for item in case.events].count("Save") == 1
    assert [item[0] for item in case.events].count("Close") == 1
    assert [item[0] for item in case.events].count("Open") == 1


@pytest.mark.parametrize("no_op", [False, True])
def test_public_sdk_exposes_resource_blocker_or_honest_noop(tmp_path, monkeypatch, no_op):
    setup = sdk_case(tmp_path, monkeypatch, no_op=no_op)
    _timed(monkeypatch, [700])
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        planned = cli.run([*setup.args, "--dry-run", "--quiet"])
        assert planned.envelope["ok"], planned.envelope
        preview = planned.envelope["data"]["preview"]
        native = preview["extensions"]["legacy_preview"]
        assert native["execution_resources"]["no_op"] is no_op
        assert native["execution_resources"]["blocked"] is not no_op
        if no_op:
            assert preview["can_apply"] is True
            done = cli.run(
                [*setup.args, "--confirm", planned.envelope["data"]["confirm_token"], "--quiet"]
            )
            assert done.envelope["ok"], done.envelope
            receipt = done.envelope["data"]["receipt"]
            assert receipt["outcome"]["persistence"] == "unchanged"
            assert receipt["extensions"]["persistence_independently_reopened"] is False
        else:
            assert preview["can_apply"] is False
            assert native["changes"] == [{"action": "write_refused", "traces": 0, "vias": 0}]
            assert any(
                item["code"] == "E_COMPONENT_EXECUTION_BUDGET" for item in native["blockers"]
            )
            refused = cli.run(
                [*setup.args, "--confirm", planned.envelope["data"]["confirm_token"], "--quiet"]
            )
            assert not refused.envelope["ok"]
            assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert setup.case.events == []
