"""The inert offline proposal cannot grant the separate scoped native startup."""

import ctypes
import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from xpedition_cli import designer_startup as S
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure startup preparation reached an external effect")

    for owner, name in (
        (Path, "open"),
        (Path, "read_bytes"),
        (Path, "write_bytes"),
        (Path, "mkdir"),
        (subprocess, "Popen"),
        (subprocess, "run"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)


def test_preparation_has_only_unrendered_fixed_startup_material():
    bundle = S.prepare_startup()
    assert bundle.scripts_ini == b"[ViewDraw]\nScript#0=cli-designer-startup.vbs\n"
    body = bundle.host_script.decode("ascii")
    assert "Set hostApplication = Application" in body
    assert 'GetObject("__PRIVATE_OWNER_OBJREF__")' in body
    assert 'OfferStartupApplication(hostApplication, "__CONNECTION_EPOCH__")' in body
    for forbidden in ("ExecuteCommand", "GetActiveObject", "Viewdraw.Application", "Shell", "Save"):
        assert forbidden not in body
    with pytest.raises(FrozenInstanceError):
        bundle.host_script = b"changed"
    with pytest.raises(TypeError):
        S.StartupPreparation(host_script=b"arbitrary script")
    manifest = bundle.manifest()
    assert manifest["mode"] == "offline_preparation"
    assert not manifest["materialized"] and not manifest["private_endpoint_rendered"]
    assert len(manifest["files"]) == 2
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])


def test_offline_proposal_readiness_cannot_be_mutated_into_native_permission(monkeypatch):
    monkeypatch.setenv("XPEDITION_NATIVE_EXECUTION_ENABLED", "1")
    first = S.startup_readiness()
    assert not first["native_launch_enabled"] and not first["native_connection_qualified"]
    assert len(first["native_launch_blockers"]) == 3
    assert first["evidence"]["startup_host_Application"] == "installed_sample"
    assert first["evidence"]["private_configuration_exclusive_selection"] == "unverified"
    first["native_launch_blockers"].clear()
    first["native_launch_enabled"] = True
    first["native_connection_qualified"] = True
    first["preparation"]["files"][0]["sha256"] = "changed"
    assert len(S.startup_readiness()["native_launch_blockers"]) == 3
    assert S.startup_readiness()["preparation"]["files"][0]["sha256"] != "changed"
    assert S.startup_readiness()["native_launch_enabled"] is False
    assert S.startup_readiness()["native_connection_qualified"] is False
    with pytest.raises(CLIError) as rejected:
        S.require_dedicated_designer()
    assert rejected.value.code == "E_HUMAN_REQUIRED"
    assert rejected.value.details["native_request_dispatched"] is False


def test_native_refusal_is_before_any_effect_and_does_not_expose_private_connection():
    with pytest.raises(CLIError) as failure:
        S.require_dedicated_designer()
    details = failure.value.details
    assert failure.value.code == "E_HUMAN_REQUIRED"
    for key in ("write_attempted", "state_write_attempted", "native_request_dispatched"):
        assert details[key] is False
    exported = json.dumps(details)
    assert "objref:" not in exported and "connection_epoch" not in exported
    assert not details["startup_readiness"]["legacy_unknown_adoption"]
    assert details["startup_readiness"]["history_is_not_a_prerequisite"]
