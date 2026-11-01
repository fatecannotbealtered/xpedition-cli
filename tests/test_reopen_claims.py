"""Public SDK closure claims require an explicitly verified lifecycle, not a logical pass."""

from __future__ import annotations

import copy

import pytest
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli import driver


@pytest.mark.parametrize(
    "outcome,no_change",
    [
        ("in_synch", None),
        ("in_synch", True),
        ("annotated", None),
        ("annotated_on_open", None),
    ],
)
def test_fresh_logical_update_can_pass_without_claiming_an_independent_reopen(
    tmp_path,
    monkeypatch,
    outcome,
    no_change,
):
    _scope(tmp_path, monkeypatch)
    project = tmp_path / "independent-logical-board.prj"
    project.write_bytes(b"synthetic project source; no actual CAD")
    source = {
        "components": [
            {
                "refdes": "J47",
                "uid": "source-J47",
                "internal_part_no": "HEADER-DEMO",
                "pins": [{"number": "1", "net": "SIGNAL"}, {"number": "2", "net": "RETURN"}],
            },
        ]
    }
    actual = {
        "components": [
            {
                "refdes": "J47",
                "uid": 147,
                "part_number": "HEADER-DEMO",
                "pins": copy.deepcopy(source["components"][0]["pins"]),
            },
        ]
    }
    calls, writes = [], []

    class Boundary:
        def invoke(self, method, params, **kwargs):
            calls.append((method, copy.deepcopy(params)))
            if method == "identity":
                return {
                    "application": {
                        "pid": 4242 if params["domain"] == "pcb" else 4343,
                        "document": {"open": True, "path": str(project)},
                    },
                    "adapter": _worker_fixture(),
                }
            assert method == "snapshot", (
                "A logical in-sync result must not fabricate lifecycle calls"
            )
            return copy.deepcopy(actual)

    def update(options):
        if options.get("dry_run"):
            return {
                "confirm_token": "synthetic-update-plan",
                "preview": {"verification_input": copy.deepcopy(source), "changes": []},
            }
        writes.append("logical-update-handler-once")
        result = {
            "outcome": outcome,
            "saved": True,
            "persistence_verified": True,
            "errors": [],
            "fixture": "reported saved/in_sync without SaveCloseOpen witness",
        }
        if no_change is not None:
            result["no_change"] = no_change
        return result

    monkeypatch.setattr(driver, "_handler", lambda command: update)
    args = ["pcb", "update", "--project", str(project), "--dangerous"]
    with driver.runtime(driver.Adapter(Boundary()), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        completed = cli.run(
            [*args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"]
        )
        assert completed.envelope["ok"], completed.envelope
        receipt = completed.envelope["data"]["receipt"]
        assert receipt["outcome"]["verification"]["status"] == "passed"
        assert receipt["outcome"]["persistence"] == ("unchanged" if no_change else "saved")
        assert receipt["extensions"]["file_readback"]["valid"] is True
        assert receipt["extensions"]["result"]["persistence_verified"] is True
        assert receipt["extensions"]["persistence_independently_reopened"] is False
        assert receipt == cli._store.get(receipt["operation_id"])["receipt"]
        old = copy.deepcopy(receipt)
        replay = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"])
        assert not replay.envelope["ok"]
        assert cli._store.get(receipt["operation_id"])["receipt"] == old
    assert writes == ["logical-update-handler-once"]
    assert len([item for item in calls if item[0] == "snapshot"]) >= 3
    assert {item[0] for item in calls} == {"identity", "snapshot"}


@pytest.mark.parametrize("no_op", [False, True])
def test_complete_saved_component_receipt_retains_reopen_claim_and_noop_does_not(
    tmp_path,
    monkeypatch,
    no_op,
):
    from test_component_connect_sdk import sdk_case

    setup = sdk_case(tmp_path, monkeypatch, no_op=no_op)
    with driver.runtime(state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*setup.args, "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        completed = cli.run(
            [*setup.args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"]
        )
        assert completed.envelope["ok"], completed.envelope
        receipt = completed.envelope["data"]["receipt"]
    assert receipt["extensions"]["persistence_independently_reopened"] is not no_op
    if no_op:
        assert setup.case.events == []
        assert receipt["outcome"]["persistence"] == "unchanged"
    else:
        lifecycle = [item[0] for item in setup.case.events]
        assert lifecycle.count("Save") == lifecycle.count("Close") == lifecycle.count("Open") == 1
        assert receipt["extensions"]["file_readback"]["persistence_verified"] is True


@pytest.mark.parametrize("no_op", [False, True])
def test_verified_batch_lifecycle_retains_reopen_claim_and_noop_does_not(
    tmp_path,
    monkeypatch,
    no_op,
):
    import json

    from test_batch_driver import NativeBoundary

    from xpedition_cli.cli import common

    _scope(tmp_path, monkeypatch)
    board = tmp_path / "own-batch.pcb"
    board.write_bytes(b"synthetic batch boundary")
    native = NativeBoundary(board, "noop" if no_op else "success")
    monkeypatch.setattr(common, "NativeBackend", lambda: native)
    task = {
        "schema_version": "1.0",
        "unit": "mm",
        "selection": [row["refdes"] for row in native.driver.rows[:3]],
        "steps": [{"op": "translate", "dx": 0 if no_op else 0.25, "dy": 0}],
    }
    task_file = tmp_path / "batch.json"
    task_file.write_text(json.dumps(task), encoding="utf-8")
    args = ["pcb", "placement", "move", "--project", str(board), "--file", str(task_file)]
    with driver.runtime(driver.Adapter(native), state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*args, "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        completed = cli.run(
            [*args, "--confirm", preview.envelope["data"]["confirm_token"], "--quiet"]
        )
        assert completed.envelope["ok"], completed.envelope
        receipt = completed.envelope["data"]["receipt"]
    assert receipt["extensions"]["persistence_independently_reopened"] is not no_op
    assert receipt["extensions"]["file_readback"]["persistence_verified"] is not no_op
    assert native.driver.calls.count("save") == (0 if no_op else 1)
