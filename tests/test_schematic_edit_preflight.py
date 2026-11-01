"""Ordinary edit protection refuses before token creation and native dispatch."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import pytest
from test_schematic_edit_durability_cli import no_native  # noqa: F401 - hard native backstop
from test_schematic_edit_protection import _public_connect_operations, _snapshot

from xpedition_cli.cli import schematic


@pytest.fixture
def scene(tmp_path, adapter, cli):
    project = tmp_path / "Owned.prj"
    project.write_bytes(b"synthetic project, no CAD")
    operations_file = tmp_path / "connect.json"
    case = NS(before=_snapshot(), operations=_public_connect_operations(), calls=[])

    def snapshot(params):
        return copy.deepcopy(case.before)

    adapter.on("snapshot", snapshot)
    args = ["schematic", "edit", "--project", str(project), "--file", str(operations_file)]

    def invoke(*extra):
        operations_file.write_text(json.dumps({"operations": case.operations}), encoding="utf-8")
        return cli(*args, *extra)

    case.run, case.adapter = invoke, adapter
    return case


@pytest.mark.parametrize(
    "bad", ["extra_field", "coordinate_range", "point_limit", "coverage", "unsupported"]
)
def test_public_preview_rejects_unprotected_parameters_before_token_or_apply(
    scene, monkeypatch, bad
):
    calls = []
    monkeypatch.setattr(schematic, "previewed", lambda *args, **kwargs: calls.append(True))
    if bad == "extra_field":
        scene.operations[1]["geometry_verified"] = True
    elif bad == "coordinate_range":
        scene.operations[1]["x"] = 2**31
    elif bad == "point_limit":
        scene.operations[1]["points"] = [[300, 480]] * 65
    elif bad == "coverage":
        scene.before["components"][0]["pin_observation_coverage"]["complete"] = False
    else:
        scene.operations = [{"type": "delete_component", "refdes": "R1"}]
    code, payload = scene.run("--dry-run")
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION", payload
    details = payload["error"]["details"]
    assert details["stage"] == "ordinary_edit_preflight"
    assert details["validation"]["valid"] is False
    assert details["write_attempted"] is details["native_request_dispatched"] is False
    assert details["state_write_attempted"] is False
    assert calls == [] and "confirm_token" not in json.dumps(payload)
    assert "apply_changeset" not in scene.adapter.methods()


def test_public_exact_connect_geometry_parameters_receive_only_a_preflight_token(scene):
    original = copy.deepcopy(scene.operations)
    code, payload = scene.run("--dry-run")
    assert code == 0, payload
    preview = payload["data"]["preview"]
    assert payload["data"]["confirm_token"]
    assert preview["steps"][1]["points"] == [[300, 480], [600, 480]]
    assert preview["steps"][1]["x"] == 450 and preview["steps"][1]["y"] == 490
    preflight = preview["protection_preflight"]
    assert preflight["valid"] is preflight["complete"] is True
    assert preflight["verification_performed"] is preflight["persistence_verified"] is False
    assert "wire_segments_routes_labels_and_other_free_graphics" in preflight["not_checked"]
    assert scene.operations == original
    assert "apply_changeset" not in scene.adapter.methods()


def test_confirm_rechecks_protection_before_consuming_token_or_applying(scene, monkeypatch):
    code, preview = scene.run("--dry-run")
    assert code == 0, preview
    confirmed = []
    monkeypatch.setattr(schematic, "confirmed", lambda *args: confirmed.append(True))
    scene.before["components"][0]["pin_observation_coverage"]["complete"] = False
    code, payload = scene.run("--confirm", preview["data"]["confirm_token"])
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION", payload
    assert payload["error"]["details"]["native_request_dispatched"] is False
    assert confirmed == [] and "apply_changeset" not in scene.adapter.methods()
