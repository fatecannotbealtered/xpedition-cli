"""The driver independently binds and verifies native edit/save/reopen timestamp evidence."""

from __future__ import annotations

import copy
import json

import pytest
from test_schematic_attribute_display_driver import _design, _scope

from xpedition_cli import __version__, driver
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.schematic_attribute_display import freeze_save_metadata, project_display

BEFORE_TIMESTAMP = "18/10/2026:12:00"
SAVED_TIMESTAMP = "18/10/2026:12:01"
FRESH_TIMESTAMP = "18/10/2026:12:02"


def _border(snapshot):
    return snapshot["unnamed_components"][0]


def _native_design():
    snapshot = _design()
    for number, sheet in enumerate(snapshot["sheets"], 1):
        sheet["number"] = number
    snapshot["unnamed_components"].append(
        {
            "refdes": "",
            "uid": "fixture-border",
            "sheet": 1,
            "pins": [],
            "attributes": {
                "@DATETIME": BEFORE_TIMESTAMP,
                "@NAME": snapshot["project"],
                "@Sheet": "1",
                "@SHEETTOTAL": "2",
                "@REVISION": "A",
            },
            "attribute_display": [],
            "identity_observation_coverage": {"complete": True, "errors": []},
            "pin_observation_coverage": {"complete": True, "errors": []},
            "attribute_display_coverage": {
                "names": [],
                "enumeration_complete": True,
                "errors": [],
                "other_text_graphics_observed": False,
            },
        }
    )
    return snapshot


def _binding(snapshot):
    binding = freeze_save_metadata(snapshot)
    assert binding is not None
    return binding


def _exercise(tmp_path, monkeypatch, *, mutate_preview=None, mutate_fresh=None, mutate_result=None):
    _scope(tmp_path, monkeypatch)
    project = tmp_path / "owned.prj"
    project.write_bytes(b"offline native save metadata fixture")
    operation = {
        "type": "set_property",
        "refdes": "R1",
        "name": "PartLabel",
        "value": "RC label",
        "display": {"x": 233, "y": 377, "visible": True},
    }
    changes = tmp_path / "display.json"
    changes.write_text(json.dumps({"operations": [operation]}), encoding="utf-8")
    before = _native_design()
    actual = copy.deepcopy(before)
    expected = copy.deepcopy(before)
    project_display(expected["components"][2], operation)
    _border(expected)["attributes"]["@DATETIME"] = SAVED_TIMESTAMP
    dispatches = []
    observations = []
    returned_results = []
    document = {
        "open": True,
        "path": str(project),
        "designs": ["Board1"],
        "status": "observed",
        "source": "IsProjectOpened/GetProjectData public getters",
    }
    application = {"pid": 4242, "active_project": str(project), "document": document}

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {
                    "application": copy.deepcopy(application),
                    "adapter": {"version": __version__, "build_sha256": "a" * 64, "fixture": True},
                }
            assert method == "snapshot"
            assert params["observe_only"] is True
            observations.append(copy.deepcopy(actual))
            return copy.deepcopy(actual)

    def handler(options):
        if options.get("dry_run"):
            preview = {
                "changes": [],
                "steps": [{**operation, "sheet": 1}],
                "save_metadata": _binding(before),
            }
            if mutate_preview:
                mutate_preview(preview)
            return {"preview": preview, "confirm_token": "private-fixture-token"}
        dispatches.append(True)
        actual.clear()
        actual.update(copy.deepcopy(expected))
        if mutate_fresh:
            mutate_fresh(actual)
        result = {
            "saved": True,
            "save_acknowledged": True,
            "observed": copy.deepcopy(expected),
            "read_back_after_reopen": copy.deepcopy(expected),
            "verification": {
                "valid": True,
                "complete": True,
                "display_verified": True,
                "protection_verified": True,
                "persistence_verified": True,
            },
            "persistence_verified": True,
            "lifecycle": {
                "source": "designer_save_close_reopen",
                "save_count": 1,
                "close_count": 1,
                "reopen_count": 1,
                "save_acknowledged": True,
                "closed_observed": True,
                "close_returned": True,
                "before_identity": copy.deepcopy(document),
                "reopened_identity": copy.deepcopy(document),
                "before_pid": 4242,
                "reopened_pid": 4242,
            },
        }
        if mutate_result:
            mutate_result(result)
        returned_results.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr(driver, "_handler", lambda command: handler)
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    applied = None
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        if planned.envelope["ok"]:
            applied = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    return {
        "planned": planned,
        "applied": applied,
        "before": before,
        "after": actual,
        "expected": expected,
        "dispatches": dispatches,
        "observations": observations,
        "returned_results": returned_results,
    }


def _receipt(exercise):
    envelope = exercise["applied"].envelope
    return (envelope.get("data") or envelope["error"]["details"])["receipt"]


def _metadata_change(after=SAVED_TIMESTAMP):
    return {
        "owner": {"refdes": "", "uid": "fixture-border", "sheet": 1},
        "field": "@DATETIME",
        "before": BEFORE_TIMESTAMP,
        "after": after,
        "source": "native_save_metadata",
    }


def test_driver_native_save_timestamp_preserves_raw_snapshots_and_reports_metadata(
    tmp_path, monkeypatch
):
    exercise = _exercise(tmp_path, monkeypatch)
    assert exercise["applied"].envelope["ok"], exercise["applied"].envelope
    assert exercise["dispatches"] == [True]
    assert len(exercise["observations"]) == 3
    receipt = _receipt(exercise)
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "saved"
    assert receipt["snapshot_before"]["extensions"]["facts"]["native"] == exercise["before"]
    assert receipt["snapshot_after"]["extensions"]["facts"]["native"] == exercise["expected"]
    readback = receipt["extensions"]["file_readback"]
    assert readback["metadata_changes"] == [_metadata_change()]
    persistence = readback["persistence_readback"]
    assert persistence["reopened_changes"]["metadata_changes"] == [_metadata_change()]
    assert persistence["reopened_current"]["metadata_changes"] == []
    assert persistence["reopened_current"]["valid"] is True
    assert _border(exercise["before"])["attributes"]["@DATETIME"] == BEFORE_TIMESTAMP


@pytest.mark.parametrize(
    "mutate_preview",
    [
        pytest.param(
            lambda preview: preview["save_metadata"]["owner"].update(uid="forged-border"),
            id="forged-owner",
        ),
        pytest.param(
            lambda preview: preview["save_metadata"].update(field="@REVISION"),
            id="forged-field",
        ),
        pytest.param(
            lambda preview: preview["save_metadata"]["owner"].update(sheet=True),
            id="boolean-owner-sheet",
        ),
        pytest.param(lambda preview: preview.pop("save_metadata"), id="omitted-binding"),
    ],
)
def test_driver_refuses_unapproved_save_metadata_during_plan(tmp_path, monkeypatch, mutate_preview):
    exercise = _exercise(tmp_path, monkeypatch, mutate_preview=mutate_preview)
    assert exercise["planned"].envelope["error"]["code"] == "E_CONFLICT"
    assert exercise["planned"].envelope["error"]["details"]["write_attempted"] is False
    assert exercise["applied"] is None
    assert exercise["dispatches"] == []
    assert exercise["after"] == exercise["before"]


def test_driver_reopened_to_fresh_timestamp_comparison_remains_strict(tmp_path, monkeypatch):
    exercise = _exercise(
        tmp_path,
        monkeypatch,
        mutate_fresh=lambda snapshot: _border(snapshot)["attributes"].update(
            {"@DATETIME": FRESH_TIMESTAMP}
        ),
    )
    assert exercise["applied"].envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    receipt = _receipt(exercise)
    readback = receipt["extensions"]["file_readback"]
    assert readback["metadata_changes"] == [_metadata_change(FRESH_TIMESTAMP)]
    persistence = readback["persistence_readback"]
    assert persistence["reopened_changes"]["valid"] is True
    assert persistence["reopened_changes"]["metadata_changes"] == [_metadata_change()]
    assert persistence["reopened_current"]["valid"] is False
    assert persistence["reopened_current"]["metadata_changes"] == []
    assert persistence["reopened_current"]["issues"]
    assert receipt["outcome"]["persistence"] == "unknown"


@pytest.mark.parametrize(
    "mutate_fresh",
    [
        pytest.param(
            lambda snapshot: _border(snapshot)["attributes"].update({"@REVISION": "B"}),
            id="other-border-field",
        ),
        pytest.param(lambda snapshot: _border(snapshot).update(uid="moved-border"), id="owner"),
    ],
)
def test_driver_save_timestamp_binding_does_not_allow_other_border_changes(
    tmp_path, monkeypatch, mutate_fresh
):
    exercise = _exercise(tmp_path, monkeypatch, mutate_fresh=mutate_fresh)
    assert exercise["applied"].envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    receipt = _receipt(exercise)
    assert receipt["extensions"]["file_readback"]["protection_verified"] is False
    assert receipt["extensions"]["file_readback"]["issues"]
    assert receipt["outcome"]["persistence"] == "unknown"


def test_driver_metadata_changes_are_computed_from_fresh_observation(tmp_path, monkeypatch):
    forged = [{**_metadata_change(), "after": "handler-forged-timestamp"}]
    exercise = _exercise(
        tmp_path,
        monkeypatch,
        mutate_result=lambda result: result.update(metadata_changes=forged),
    )
    assert exercise["applied"].envelope["ok"], exercise["applied"].envelope
    receipt = _receipt(exercise)
    assert receipt["extensions"]["result"]["metadata_changes"] == forged
    assert receipt["extensions"]["file_readback"]["metadata_changes"] == [_metadata_change()]
    assert receipt["snapshot_after"]["extensions"]["facts"]["native"] == exercise["expected"]
