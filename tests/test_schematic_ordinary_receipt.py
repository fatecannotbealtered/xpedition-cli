"""Real pinned SDK receipts with synthetic native getter/handler boundaries.

The SDK plans, signs, consumes and rejects replay of its actual token. No COM,
CAD, process query, owner launch or old runtime is used by these fixtures.
"""

from __future__ import annotations

import copy

import pytest
from test_schematic_attribute_display_driver import _scope
from test_schematic_edit_durability_cli import edit_scene, no_native  # noqa: F401
from test_schematic_ordinary_driver import _observations

from xpedition_cli import __version__, driver
from xpedition_cli import native_process as P
from xpedition_cli.driver import Adapter, runtime


@pytest.fixture(autouse=True)
def no_process_access(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Ordinary receipt tests cannot query processes or enter native code")

    monkeypatch.setattr(P, "processes", forbidden)
    monkeypatch.setattr(P, "ProcessHandle", forbidden)
    yield
    assert hits == []


def _receipt(case, tmp_path, monkeypatch, *, failure=None):
    _scope(tmp_path, monkeypatch)
    original_application = _observations(case)[0]["extensions"]["facts"]["application"]
    application = copy.deepcopy(original_application)
    actual = copy.deepcopy(case.before)
    dispatches, reads, identities = [], [], []

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                identities.append(copy.deepcopy(application))
                return {
                    "application": copy.deepcopy(application),
                    "adapter": {"version": __version__, "build_sha256": "a" * 64, "fixture": True},
                }
            assert method == "snapshot" and params["observe_only"] is True
            reads.append(copy.deepcopy(actual))
            return copy.deepcopy(actual)

    def handler(options):
        if options.get("dry_run"):
            return {
                "preview": {"steps": [{**case.operation, "sheet": 1}], "save_metadata": None},
                "confirm_token": "synthetic-inner-handler-token",
            }
        dispatches.append(True)
        actual.clear()
        actual.update(copy.deepcopy(case.fresh))
        result = copy.deepcopy(case.written)
        # Both false flags and forged true flags are deliberately independent
        # from the Driver's own identity/getter comparisons.
        if failure is not None:
            result.update(persistence_verified=True, verification={"valid": True})
        if failure == "save_only":
            result = {"saved": True, "persistence_verified": True, "verification": {"valid": True}}
        elif failure == "missing_reopen":
            result.pop("read_back_after_reopen")
        elif failure == "wrong_epoch":
            application["dedicated_startup_binding"]["connection_epoch_sha256"] = "b" * 64
        elif failure == "wrong_birth":
            application["pid_source"]["matching_process"]["birth_filetime_100ns"] = (
                "134000000000000002"
            )
        elif failure == "non_target":
            next(part for part in actual["components"] if part["refdes"] == "C1")["x"] += 10
        elif failure == "reopen_records":
            next(
                part
                for part in result["read_back_after_reopen"]["components"]
                if part["refdes"] == "C1"
            )["x"] += 10
        return result

    monkeypatch.setattr(driver, "_handler", lambda command: handler)
    args = [
        "schematic",
        "edit",
        "--project",
        str(case.project),
        "--file",
        str(case.project.parent / "operations.json"),
    ]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "sdk-state") as sdk:
        planned = sdk.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        token = planned.envelope["data"]["confirm_token"]
        answer = sdk.run([*args, "--confirm", token])
        payload = answer.envelope.get("data") or answer.envelope["error"]["details"]
        receipt = payload["receipt"]
        record = sdk._store_open().get(receipt["operation_id"])
        receipt_before_replay = copy.deepcopy(record["receipt"])
        replay = sdk.run([*args, "--confirm", token])
        assert not replay.envelope["ok"]
        assert sdk._store_open().get(receipt["operation_id"])["receipt"] == receipt_before_replay
    assert len(dispatches) == 1 and len(reads) == 3
    assert reads[:2] == [case.before, case.before]
    assert identities
    assert record["operation_id"] == record["plan"]["operation_id"] == receipt["operation_id"]
    assert record["plan_id"] == record["plan"]["plan_id"] == receipt["plan_id"]
    return answer, receipt, record


def test_real_sdk_ordinary_receipt_qualifies_from_independent_evidence_despite_false_flags(
    edit_scene,  # noqa: F811 - shared complete synthetic records
    tmp_path,
    monkeypatch,
):
    answer, receipt, record = _receipt(edit_scene, tmp_path, monkeypatch)
    assert answer.envelope["ok"], answer.envelope
    outcome = receipt["outcome"]
    assert record["status"] == "succeeded"
    assert outcome["verification"]["status"] == "passed"
    assert outcome["verification"]["coverage"] == "complete"
    assert outcome["persistence"] == "saved" and outcome["change"] == "applied"
    assert receipt["extensions"]["result"]["persistence_verified"] is False
    assert receipt["extensions"]["persistence_independently_reopened"] is True
    observed = receipt["extensions"]["file_readback"]
    assert observed["valid"] is observed["complete"] is observed["persistence_verified"] is True
    assert observed["identity_verified"] is observed["host_object_binding_verified"] is True
    expected_checks = {
        "schematic-edit-protected-records",
        "schematic-edit-host-and-document",
        "schematic-edit-save-close-open",
        "schematic-edit-reopened-records",
    }
    checks = [row for row in outcome["verification"]["checks"] if row["id"] in expected_checks]
    assert {row["id"] for row in checks} == expected_checks
    assert all(row["required"] is True and row["status"] == "passed" for row in checks)
    assert all(row["evidence_ref"] == receipt["snapshot_after"]["id"] for row in checks)


@pytest.mark.parametrize(
    "failure",
    ["save_only", "missing_reopen", "wrong_epoch", "wrong_birth", "non_target", "reopen_records"],
)
def test_real_sdk_ordinary_receipt_rejects_forged_flags_and_unbound_or_changed_observations(
    edit_scene,  # noqa: F811 - shared complete synthetic records
    tmp_path,
    monkeypatch,
    failure,
):
    answer, receipt, record = _receipt(edit_scene, tmp_path, monkeypatch, failure=failure)
    assert not answer.envelope["ok"]
    assert answer.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert record["status"] == "unknown" and record["resolution"] is None
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["outcome"]["verification"]["status"] in {"failed", "incomplete"}
    assert receipt["outcome"]["verification"]["coverage"] == "partial"
    assert receipt["extensions"]["result"]["persistence_verified"] is True
    assert receipt["extensions"]["file_readback"]["valid"] is False
    assert receipt["extensions"]["persistence_independently_reopened"] is False
    checks = receipt["outcome"]["verification"]["checks"]
    assert any(row["required"] is True and row["status"] != "passed" for row in checks)
