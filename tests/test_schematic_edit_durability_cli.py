"""CLI independently checks ordinary edits and actual lifecycle observations.

All native calls are answered by the normal FakeAdapter boundary. Synthetic
records are explicit complete observations, never a live CAD or COM fixture.
"""

from __future__ import annotations

import copy
import ctypes
import json
from types import SimpleNamespace as NS

import pytest
from fakes import Failure
from test_schematic_attribute_display_driver import _design

from xpedition_cli import native_com_adapter as A
from xpedition_cli.cli.schematic import verify_ordinary_edit_durability
from xpedition_cli.models import normalise_project
from xpedition_cli.schematic_attribute_display import DISPLAY_NAMES


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("CLI durability tests must not activate COM or use a native API")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden)
    monkeypatch.setattr(A, "_import_com", forbidden)
    yield
    assert hits == []


@pytest.fixture
def edit_scene(tmp_path, cli, adapter):
    project = tmp_path / "Dedicated.prj"
    project.write_bytes(b"synthetic, not a native project")
    changes = tmp_path / "operations.json"
    operation = {"type": "set_property", "refdes": "R1", "name": "Value", "value": "5.1k"}
    changes.write_text(json.dumps({"operations": [operation]}), encoding="utf-8")
    before = _design()
    # This fixture declares full native getter values, unlike the display-only
    # fixture whose deliberately unavailable optional Size getter is protected.
    before["metadata"] = {"backend": "native_xpedition", "source": "Viewdraw.Application"}
    before["sheets"] = [
        {"number": number, "name": f"Board1.{number}", "parts": 4} for number in (1, 2)
    ]
    for net in before["nets"]:
        net.update(unnamed=False, sheets=[1, 2])
    for part in before["components"]:
        part.update(
            name=part["internal_part_no"], description="", manufacturer="", mpn="", rotation=0
        )
        part["attribute_display_coverage"].update(
            names=list(DISPLAY_NAMES),
            truncated=False,
            attribute_count=len(part["attributes"]),
            attribute_limit=256,
        )
        for index, pin in enumerate(part["pins"]):
            pin.update(net_unnamed=False, x=part["x"] + 20 * index, y=part["y"], no_connect=False)
        for attribute in part["attribute_display"]:
            attribute["display"]["size"] = 10
            attribute["getter_errors"] = {}
    after = copy.deepcopy(before)
    component = next(row for row in after["components"] if row["refdes"] == "R1")
    component["value"] = component["attributes"]["Value"] = "5.1k"
    for attribute in component["attribute_display"]:
        if attribute["name"] == "Value":
            attribute["value"] = "5.1k"
    document = {
        "open": True,
        "path": str(project),
        "designs": ["Board1"],
        "status": "observed",
        "source": "dedicated_startup_host_Application_and_authenticated_ncalrpc",
    }
    lifecycle = {
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
    }
    written = {
        "domain": "schematic",
        "applied": [{"type": "set_property", "refdes": "R1"}],
        "saved": True,
        "save_acknowledged": True,
        "persistence_verified": False,
        "verification_source": "native_getters_after_edit_save_close_open",
        "lifecycle": lifecycle,
        "read_back_before_save": copy.deepcopy(after),
        "read_back_after_reopen": copy.deepcopy(after),
        "save_metadata": None,
        "verification": {"valid": False, "protection_verified": False},  # Flags are not proof.
    }
    case = NS(
        project=project,
        operation=operation,
        before=before,
        fresh=after,
        written=written,
        snapshot_calls=0,
        fresh_error=False,
        upstream_error=None,
    )

    def snapshot(params):
        case.snapshot_calls += 1
        if case.snapshot_calls <= 2:
            return copy.deepcopy(case.before)
        if case.fresh_error:
            raise Failure("E_TIMEOUT", "synthetic independent snapshot timeout")
        return copy.deepcopy(case.fresh)

    def apply(params):
        assert params["save"] is True and params["save_metadata"] is None
        if case.upstream_error:
            raise case.upstream_error
        return copy.deepcopy(case.written)

    adapter.on("snapshot", snapshot)
    adapter.on("apply_changeset", apply)
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    case.preview = lambda: cli(*args, "--dry-run")
    case.confirm = lambda token: cli(*args, "--confirm", token)
    case.adapter = adapter
    return case


def _run(case):
    code, preview = case.preview()
    assert code == 0, preview
    return case.confirm(preview["data"]["confirm_token"])


def test_ordinary_edit_persistence_is_derived_from_records_not_handler_flags(edit_scene):
    case = edit_scene
    original = copy.deepcopy(case.written)
    code, result = _run(case)
    assert code == 0, json.dumps(
        result.get("error", {}).get("details", {}).get("verification", {}), indent=2
    )
    actual = result["data"]
    assert actual["persistence_verified"] is True
    assert actual["verification"]["persistence_readback"]["protection_verified"] is True
    assert actual["read_back_after_reopen"] == case.written["read_back_after_reopen"]
    assert actual["lifecycle"] == case.written["lifecycle"]
    independent = verify_ordinary_edit_durability(
        case.project,
        normalise_project(case.before),
        normalise_project(case.fresh),
        [case.operation],
        actual,
        save_metadata=None,
    )
    assert independent["valid"] is True  # Same evidence remains consumable by the SDK driver.
    assert case.written == original and case.snapshot_calls == 3
    assert case.adapter.methods().count("apply_changeset") == 1
    assert not set(case.adapter.methods()) & {"save", "close", "open"}


@pytest.mark.parametrize(
    "bad",
    [
        "save_false",
        "save_ack",
        "reopen_count",
        "bool_count",
        "closed_observed",
        "close_reply",
        "before_pid",
        "reopened_path",
        "identity_source",
        "missing_before_save",
        "missing_reopen",
        "source",
        "metadata_binding",
        "failure",
    ],
)
def test_flags_cannot_qualify_incomplete_lifecycle_or_missing_observations(edit_scene, bad):
    case = edit_scene
    case.written["persistence_verified"] = True
    life = case.written["lifecycle"]
    if bad == "save_false":
        case.written["saved"] = False
    elif bad == "save_ack":
        case.written["save_acknowledged"] = False
    elif bad == "reopen_count":
        life["reopen_count"] = 0
    elif bad == "bool_count":
        life["save_count"] = True
    elif bad == "closed_observed":
        life["closed_observed"] = False
    elif bad == "close_reply":
        life["close_returned"] = False
    elif bad == "before_pid":
        life["before_pid"] = 9999
    elif bad == "reopened_path":
        life["reopened_identity"]["path"] = str(case.project.with_name("Different.prj"))
    elif bad == "identity_source":
        life["reopened_identity"]["source"] = "echoed request"
    elif bad == "missing_before_save":
        case.written.pop("read_back_before_save")
    elif bad == "missing_reopen":
        case.written.pop("read_back_after_reopen")
    elif bad == "source":
        case.written["verification_source"] = "request echo"
    elif bad == "metadata_binding":
        case.written["save_metadata"] = {"invented": True}
    else:
        case.written["failure"] = {"stage": "after_reopen"}
    code, result = _run(case)
    assert code == 2 and result["error"]["code"] == "E_PROJECT_INVALID"
    details = result["error"]["details"]
    assert details["persistence_verified"] is False
    assert details["applied"] == case.written["applied"]
    assert details["lifecycle"] == life and details["first_failure"]
    assert case.adapter.methods().count("apply_changeset") == 1


@pytest.mark.parametrize("bad", ["attribute", "uid", "pin_net", "net", "connection", "sheet"])
def test_independent_fresh_read_detects_non_target_damage(edit_scene, bad):
    case = edit_scene
    non_target = next(row for row in case.fresh["components"] if row["refdes"] == "R2")
    if bad == "attribute":
        non_target["attributes"]["Part Number"] = "different-part"
    elif bad == "uid":
        non_target["uid"] = "different-owner"
    elif bad == "pin_net":
        non_target["pins"][0]["net"] = "different-net"
    elif bad == "net":
        case.fresh["nets"][0]["name"] = "different-net"
    elif bad == "connection":
        case.fresh["connections"][0]["pins"].remove("R2.1")
    else:
        case.fresh["sheets"][0]["name"] = "different-sheet"
    code, result = _run(case)
    assert code == 2 and result["error"]["details"]["persistence_verified"] is False
    proof = result["error"]["details"]["verification"]["persistence_readback"]
    assert proof["protection_verified"] is False
    assert case.adapter.methods().count("apply_changeset") == 1


def test_readback_failure_retains_ordinary_lifecycle_without_synthesizing_snapshot(edit_scene):
    case = edit_scene
    case.fresh_error = True
    code, result = _run(case)
    assert code == 2
    details = result["error"]["details"]
    assert details["stage"] == "read_back"
    assert details["lifecycle"] == case.written["lifecycle"]
    assert details["read_back_after_reopen"] == case.written["read_back_after_reopen"]
    assert details["applied"] == case.written["applied"]
    assert "observed" not in details and details["persistence_verified"] is False


def test_original_native_phase_failure_is_not_masked_or_replayed(edit_scene):
    case = edit_scene
    case.upstream_error = Failure(
        "E_NATIVE_FAILURE",
        "synthetic CloseProject failure",
        {
            "stage": "close_project",
            "applied": copy.deepcopy(case.written["applied"]),
            "lifecycle": copy.deepcopy(case.written["lifecycle"]),
            "persistence_verified": False,
        },
    )
    code, result = _run(case)
    assert code != 0
    assert result["error"]["details"] == case.upstream_error.details
    assert case.snapshot_calls == 2 and case.adapter.methods().count("apply_changeset") == 1


def test_legacy_saved_boolean_has_no_durability_qualification(edit_scene):
    case = edit_scene
    case.written = {"saved": True, "applied": [{"type": "set_property"}]}
    code, result = _run(case)
    assert code == 0
    assert result["data"]["persistence_verified"] is False
    assert result["data"]["verification"]["persistence_readback"]["evidence_present"] is False
