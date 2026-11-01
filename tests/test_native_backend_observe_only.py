"""Native reads explicitly request observation at the JSON adapter boundary."""

from __future__ import annotations

import os

import pytest
from fakes import schematic_snapshot

from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.errors import CLIError


@pytest.mark.parametrize("domain", [None, "schematic", "pcb"])
@pytest.mark.parametrize("with_project", [False, True])
def test_load_always_requests_observation_without_inherited_runtime_flags(
    adapter, project, monkeypatch, domain, with_project
):
    monkeypatch.delenv("XPEDITION_OBSERVE_ONLY", raising=False)
    monkeypatch.delenv("XPEDITION_REQUIRE_EXISTING", raising=False)
    backend = NativeBackend()
    backend.read_timeout_seconds = 17.5
    design, source = backend.load(str(project) if with_project else None, domain)
    expected = {"observe_only": True}
    if with_project:
        expected["project"] = str(project.resolve())
    if domain:
        expected["domain"] = domain
    assert adapter.methods() == ["snapshot"]
    assert adapter.last("snapshot") == expected
    assert adapter.calls[0]["timeout"] == 17.5
    assert source == (project.resolve() if with_project else None)
    assert isinstance(design["components"], list)
    assert "XPEDITION_OBSERVE_ONLY" not in os.environ
    assert "XPEDITION_REQUIRE_EXISTING" not in os.environ


@pytest.mark.parametrize("code", ["E_CONFLICT", "E_BACKEND_UNAVAILABLE", "E_HUMAN_REQUIRED"])
def test_unavailable_or_wrong_open_project_is_reported_without_open_or_start_fallback(
    adapter, project, code
):
    adapter.fail(
        "snapshot",
        code,
        "fixture requires the requested project to be already open",
        {"write_attempted": False},
    )
    with pytest.raises(CLIError) as raised:
        NativeBackend().load(str(project), domain="schematic")
    assert raised.value.code == code
    assert raised.value.details["write_attempted"] is False
    assert adapter.methods() == ["snapshot"]
    assert adapter.last("snapshot")["observe_only"] is True
    assert adapter.last("snapshot")["project"] == str(project.resolve())


def test_load_keeps_existing_tuple_and_native_attribute_observations(adapter, project):
    snapshot = schematic_snapshot()
    owner = {"refdes": "U1", "uid": "fixture-U1", "sheet": 1}
    record = {
        "name": "Value",
        "value": "native getter value",
        "owner": owner,
        "display": {"x": 101, "y": 202, "visible": 3, "units": "sheet_unit"},
        "getter_errors": {"size": "unavailable"},
    }
    component = snapshot["components"][0]
    component["attributes"] = {"Value": "native getter value"}
    component["attribute_display"] = [record]
    component["attribute_display_coverage"] = {
        "enumeration_complete": True,
        "other_text_graphics_observed": False,
    }
    adapter.on("snapshot", snapshot)
    observed, source = NativeBackend().load(str(project), "schematic")
    assert source == project.resolve()
    assert observed["components"][0]["attributes"] == {"Value": "native getter value"}
    assert observed["components"][0]["attribute_display"] == [record]
    assert (
        observed["components"][0]["attribute_display_coverage"]
        == component["attribute_display_coverage"]
    )
    assert "size" not in observed["components"][0]["attribute_display"][0]["display"]
    assert observed["connections"] == snapshot["connections"]
    assert adapter.methods() == ["snapshot"]
