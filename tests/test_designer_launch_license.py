"""Owner launch license inheritance with a fake registry and no native calls."""

from __future__ import annotations

import ctypes
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_designer_native_launch import ENDPOINT, EPOCH, PLAN, _launch_inputs_fixture

from xpedition_cli import designer_native_launch as N
from xpedition_cli import native_com_adapter as A

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owner environment")
LICENSE_KEYS = ("MGLS_LICENSE_FILE", "SALT_LICENSE_SERVER", "LM_LICENSE_FILE")


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("License test attempted a native or COM boundary")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden)
    monkeypatch.setattr(A, "_import_com", forbidden)
    monkeypatch.setattr(A, "_configure_environment", forbidden)
    yield
    assert hits == []


def launch_scene(monkeypatch, registered, initial=None, *, fallback_exists=True):
    events, retained = _launch_inputs_fixture(monkeypatch)
    parent_environment = dict(os.environ)
    owner_environment = {"PATH": "synthetic-system", **(initial or {})}
    # Separate mappings model the owner subprocess without changing this test
    # runner's (the caller's) environment or any machine/user setting.
    owner_os = NS(name="nt", environ=owner_environment, pathsep=os.pathsep)
    monkeypatch.setattr(N, "os", owner_os)
    monkeypatch.setattr(A, "os", owner_os)
    queries = []

    def query(hive, name):
        queries.append((hive, name))
        if (hive, name) not in registered:
            raise FileNotFoundError
        value = registered[(hive, name)]
        return value if isinstance(value, tuple) else (value, 1)

    registry = NS(
        HKEY_CURRENT_USER="user",
        HKEY_LOCAL_MACHINE="machine",
        REG_SZ=1,
        REG_EXPAND_SZ=2,
        OpenKey=lambda hive, path: nullcontext(hive),
        QueryValueEx=query,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(Path, "is_file", lambda path: path.name != "LICENSE.DAT" or fallback_exists)
    captured = []
    spawn = N.subprocess.Popen

    def capture(arguments, **options):
        captured.append(dict(options["env"]))
        return spawn(arguments, **options)

    monkeypatch.setattr(N.subprocess, "Popen", capture)
    return NS(
        events=events,
        retained=retained,
        owner=owner_environment,
        parent=parent_environment,
        queries=queries,
        captured=captured,
    )


def test_owner_launch_inherits_user_then_machine_configuration(monkeypatch):
    scene = launch_scene(
        monkeypatch,
        {
            ("user", "MGLS_LICENSE_FILE"): "fixture-user-license",
            ("machine", "MGLS_LICENSE_FILE"): "fixture-machine-license",
            ("user", "SALT_LICENSE_SERVER"): "fixture-user-salt",
            ("machine", "SALT_LICENSE_SERVER"): "fixture-machine-salt",
            ("machine", "LM_LICENSE_FILE"): "fixture-machine-lm",
        },
    )
    N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert scene.captured[0]["MGLS_LICENSE_FILE"] == "fixture-user-license"
    assert scene.captured[0]["SALT_LICENSE_SERVER"] == "fixture-user-salt"
    assert scene.captured[0]["LM_LICENSE_FILE"] == "fixture-machine-lm"
    assert ("machine", "MGLS_LICENSE_FILE") not in scene.queries
    assert ("machine", "SALT_LICENSE_SERVER") not in scene.queries
    assert dict(os.environ) == scene.parent
    assert scene.events.count("spawn") == 1


@pytest.mark.parametrize("name", LICENSE_KEYS)
@pytest.mark.parametrize("value", ["", "fixture-explicit-license"])
def test_owner_launch_preserves_explicit_process_values(monkeypatch, name, value):
    scene = launch_scene(
        monkeypatch,
        {("user", key): "fixture-registered-license" for key in LICENSE_KEYS},
        {name.lower(): value},
    )
    N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert scene.captured[0][name.lower()] == value
    assert not any(queried_name == name for _, queried_name in scene.queries)
    assert dict(os.environ) == scene.parent


def test_owner_launch_expands_all_configured_values_before_copying_child(monkeypatch):
    scene = launch_scene(
        monkeypatch,
        {
            ("user", "MGLS_LICENSE_FILE"): ("%salt_license_server%/license", 2),
            ("user", "SALT_LICENSE_SERVER"): ("%LICENSE_ROOT%/salt", 2),
        },
        {"LICENSE_ROOT": "fixture-root"},
    )
    N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert scene.captured[0]["MGLS_LICENSE_FILE"] == "fixture-root/salt/license"
    assert scene.captured[0]["SALT_LICENSE_SERVER"] == "fixture-root/salt"
    assert dict(os.environ) == scene.parent


@pytest.mark.parametrize(
    "mgls,salt,reason",
    [
        ("%MISSING_FIXTURE%", "valid-fixture", "unresolved_reference"),
        ("%SALT_LICENSE_SERVER%", "%MGLS_LICENSE_FILE%", "cyclic_reference"),
    ],
)
def test_invalid_expansion_does_not_partially_mutate_or_dispatch(monkeypatch, mgls, salt, reason):
    scene = launch_scene(
        monkeypatch,
        {
            ("user", "MGLS_LICENSE_FILE"): (mgls, 2),
            ("user", "SALT_LICENSE_SERVER"): (salt, 2),
            ("user", "LM_LICENSE_FILE"): "valid-fixture-lm",
        },
    )
    before = dict(scene.owner)
    with pytest.raises(N.LaunchError) as caught:
        N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    assert caught.value.dispatched is False
    assert caught.value.__cause__.code == "E_CONFIG"
    assert caught.value.__cause__.details["reason"] == reason
    assert scene.owner == before
    assert scene.captured == []
    assert "spawn" not in scene.events
    assert dict(os.environ) == scene.parent


@pytest.mark.parametrize("fallback_exists", [True, False])
def test_no_configured_license_retains_existing_installed_fallback(monkeypatch, fallback_exists):
    scene = launch_scene(monkeypatch, {}, fallback_exists=fallback_exists)
    N.prepare_and_launch(PLAN, ENDPOINT, EPOCH)
    expected = str(Path(PLAN["installation_root"]).parent.parent / "LICENSE.DAT")
    assert scene.captured[0].get("MGLS_LICENSE_FILE") == (expected if fallback_exists else None)
    assert "MGLS_LICENSE_FILE" not in scene.owner
    assert "SALT_LICENSE_SERVER" not in scene.captured[0]
    assert "LM_LICENSE_FILE" not in scene.captured[0]
    assert dict(os.environ) == scene.parent
