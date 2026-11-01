"""Ordinary Designer edit durability: pure fakes, no CAD/COM/OS activation."""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A
from xpedition_cli.schematic_attribute_display import DISPLAY_NAMES


def _record(refdes, uid, x):
    owner = {"refdes": refdes, "uid": uid, "sheet": 1}
    return {
        "refdes": refdes,
        "uid": uid,
        "internal_part_no": "R0603",
        "name": "R0603",
        "value": "R0603",
        "mpn": "",
        "manufacturer": "",
        "description": "",
        "package": "",
        "rotation": 0,
        "x": x,
        "y": 50,
        "sheet": 1,
        "attributes": {"Part Number": "R0603"},
        "attribute_display": [
            {
                "owner": owner,
                "name": "Part Number",
                "value": "R0603",
                "getter_errors": {},
                "display": {
                    "x": x,
                    "y": 50,
                    "units": "sheet_unit",
                    "visible": 0,
                    "size": 10,
                    "orientation": 0,
                    "origin": 1,
                },
            }
        ],
        "attribute_display_coverage": {
            "enumeration_complete": True,
            "errors": [],
            "truncated": False,
            "attribute_count": 1,
            "attribute_limit": 256,
            "names": list(DISPLAY_NAMES),
        },
        "identity_observation_coverage": {"complete": True, "errors": []},
        "pin_observation_coverage": {"complete": True, "errors": []},
        "pins": [
            {
                "number": "1",
                "net": None,
                "net_unnamed": False,
                "x": x,
                "y": 50,
                "no_connect": False,
            },
            {
                "number": "2",
                "net": None,
                "net_unnamed": False,
                "x": x,
                "y": 60,
                "no_connect": False,
            },
        ],
    }


@pytest.fixture
def edit(monkeypatch):
    state = NS(
        events=[], records=[_record("R1", "$1I1", 10), _record("R2", "$1I2", 60)], failure=None
    )
    state.app = NS(ActiveDocument=NS(Save=lambda: pytest.fail("No extra sheet save")))
    state.params = {
        "project": "C:/own/fixture.prj",
        "operations": [{"type": "move_component", "refdes": "R1", "x": 25, "y": 50, "sheet": 1}],
    }

    def snapshot(*a, **k):
        state.events.append("snapshot")
        return {
            "project": "Board1",
            "revision": "native",
            "components": copy.deepcopy(state.records),
            "unnamed_components": [],
            "unnamed_symbols": 0,
            "nets": [],
            "connections": [],
            "sheets": [{"number": 1, "name": "Board1.1", "parts": 2}],
            "metadata": {"backend": "native_xpedition", "source": "Viewdraw.Application"},
        }

    def apply(app, operation, *a):
        state.events.append("apply")
        if state.failure == "apply":
            raise A.AdapterError(
                "E_SERVER", "synthetic operation outcome unknown", {"orphan_placed": None}
            )
        state.records[0]["x"] = operation["x"]
        for item in state.records[0]["attribute_display"]:
            item["display"]["x"] = operation["x"]
        for pin in state.records[0]["pins"]:
            pin["x"] = operation["x"]
        return {"type": operation["type"], "refdes": operation["refdes"], "applied": True}

    def persist(app, project):
        assert app is state.app and project == state.params["project"]
        lifecycle = {
            "source": "designer_save_close_reopen",
            "save_count": 1,
            "close_count": 0,
            "reopen_count": 0,
            "save_acknowledged": True,
            "closed_observed": False,
            "close_returned": None,
        }
        state.events.append("SaveAll")
        if state.failure == "close":
            error = ValueError("synthetic close failure")
            error.lifecycle = lifecycle
            raise error
        state.events.extend(["CloseProject", "empty208", "OpenProject227"])
        lifecycle.update(close_count=1, reopen_count=1, closed_observed=True, close_returned=True)
        if state.failure == "lost_edit":
            state.records[0]["x"] = 10
        elif state.failure == "non_target":
            state.records[1]["attributes"]["Part Number"] = "OTHER"
        elif state.failure == "duplicate_uid":
            state.records[1]["uid"] = "$1I1"
        elif state.failure == "permutation":
            state.records.reverse()
            for row in state.records:
                row["pins"].reverse()
        return lifecycle

    monkeypatch.setattr(A, "_owner_application", lambda: state.app)
    monkeypatch.setattr(A, "_designer_snapshot", snapshot)
    monkeypatch.setattr(A, "_apply_designer_operation", apply)
    monkeypatch.setattr(A, "_active_sheet", lambda app: 1)
    monkeypatch.setattr(A, "_ensure_sheet", lambda *a: None)
    monkeypatch.setattr(D, "persist_changeset_project", persist)
    state.run = lambda: A._apply_designer_edit_changes(state.app, state.params, None, "Board1")
    return state


@pytest.mark.parametrize("failure", [None, "permutation"])
def test_normal_edit_durability_requires_actual_full_lifecycle_and_fresh_complete_records(
    edit, failure
):
    edit.failure = failure
    result = edit.run()
    assert result["persistence_verified"] is result["protection"]["valid"] is True
    assert result["verification_source"] == "native_getters_after_edit_save_close_open"
    assert result["read_back_before_save"]["components"][0]["x"] == 25
    assert [edit.events.count(name) for name in ("SaveAll", "CloseProject", "OpenProject227")] == [
        1,
        1,
        1,
    ]
    assert edit.events[-1] == "snapshot"


@pytest.mark.parametrize("failure", ["lost_edit", "non_target", "duplicate_uid", "close"])
def test_no_false_persistence_when_reopen_loses_change_non_target_drifts_or_close_fails(
    edit, failure
):
    edit.failure = failure
    with pytest.raises(A.AdapterError) as caught:
        edit.run()
    assert caught.value.details["persistence_verified"] is False
    assert caught.value.details["save_acknowledged"] is True
    assert len(caught.value.details["applied"]) == 1
    assert edit.events.count("SaveAll") == 1
    assert edit.events.count("OpenProject227") == (0 if failure == "close" else 1)


@pytest.mark.parametrize("save,failure", [(False, None), (True, "apply")])
def test_unsaved_or_failed_batch_never_adds_save_close_reopen(edit, save, failure):
    edit.params["save"], edit.failure = save, failure
    if failure:
        with pytest.raises(A.AdapterError) as caught:
            edit.run()
        assert caught.value.details["persistence_verified"] is False
        assert caught.value.details["write_attempted"] is True
        assert caught.value.details["orphan_placed"] is None
    else:
        assert edit.run()["persistence_verified"] is False
    assert not set(edit.events) & {"SaveAll", "CloseProject", "OpenProject227"}


def test_owner_keeps_core_failure_evidence_when_large_observation_is_omitted():
    from xpedition_cli import designer_native_owner as O

    error = A.AdapterError(
        "E_PROJECT_INVALID",
        "fixed failure",
        {
            "applied": [{"refdes": "R1", "applied": True}],
            "orphan_placed": None,
            "saved": True,
            "save_acknowledged": True,
            "persistence_verified": False,
            "lifecycle": {"save_count": 1, "close_count": 1, "reopen_count": 0},
            "observed": {"native_text": "x" * 200000},
        },
    )
    detail = O._adapter_failure(error)["details"]
    assert detail["applied"] == [{"refdes": "R1", "applied": True}]
    assert detail["orphan_placed"] is None and detail["saved"] is True
    assert detail["lifecycle"]["reopen_count"] == 0
    assert "observed" not in detail
    assert detail["omitted_native_edit_details"]["observed"]["observation_complete"] is False
    assert detail["native_edit_failure_details_complete"] is False
    assert detail["persistence_verified"] is False
