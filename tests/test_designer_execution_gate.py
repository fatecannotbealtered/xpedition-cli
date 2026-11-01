"""The unqualified CAD path is denied before ownership, OS, COM or workers."""

from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_host_bootstrap as H
from xpedition_cli import driver, session
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_process as P
from xpedition_cli import session_ownership as O
from xpedition_cli.backends import native_xpedition as B
from xpedition_cli.cli import sessions as S
from xpedition_cli.cli.registry import by_path
from xpedition_cli.errors import CLIError

INVOKE = B.NativeBackend.invoke


@pytest.fixture(autouse=True)
def no_old_resources(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Disabled CAD execution reached state, OS, COM or worker construction")

    for owner, name in (
        (S, "read_state"),
        (session, "read_state"),
        (O, "pointer_for"),
        (driver, "_owned_process_identity"),
        (P, "_startup_snapshot"),
        (P, "process_identity"),
        (A, "_import_com"),
        (A, "_viewdraw_active"),
        (H, "observe"),
        (H, "template_identity"),
        (threading, "Thread"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    yield
    assert hits == [], "A swallowed native/state attempt still fails the gate test"


def options():
    return {
        "host_bootstrap": "identity",
        "kind": "schematic",
        "pid": 123,
        "startup_operation_id": "op-offline-original-placeholder",
    }


def legacy_target():
    return {
        "mode": "session",
        "instance_id": "123",
        "extensions": {"command": "session attach", "host_bootstrap_request": {"kind": "identity"}},
    }


def assert_blocked(error):
    assert error.code == "E_HUMAN_REQUIRED"
    assert error.details["write_attempted"] is False
    assert error.details["state_write_attempted"] is False
    assert error.details["native_request_dispatched"] is False
    assert error.details["native_execution_enabled"] is False


def test_default_native_gate_cannot_be_enabled_by_environment_or_packet(monkeypatch):
    monkeypatch.setenv("XPEDITION_NATIVE_EXECUTION_ENABLED", "1")
    with (
        B.host_bootstrap_ownership({"native_execution_enabled": True}),
        pytest.raises(CLIError) as failure,
    ):
        B.require_designer_host_execution()
    assert_blocked(failure.value)


@pytest.mark.parametrize("mode", ["preview", "legacy-confirm"])
def test_public_sdk_gate_precedes_scope_store_and_plan(tmp_path, monkeypatch, mode):
    adapter = driver.Adapter()
    with driver.runtime(adapter, state_dir=tmp_path / "new-offline-sdk") as cli:

        def forbidden(*args, **kwargs):
            pytest.fail("The blocked public request must not open a scope, store or plan")

        for owner, name in (
            (cli, "_scope"),
            (cli, "_store_open"),
            (adapter, "plan"),
            (adapter, "revision"),
            (adapter, "apply"),
        ):
            monkeypatch.setattr(owner, name, forbidden)
        args = [
            "session",
            "attach",
            "--kind",
            "schematic",
            "--pid",
            "123",
            "--startup-operation-id",
            "op-offline-original-placeholder",
            "--host-bootstrap",
            "identity",
        ]
        args += (
            ["--dry-run"]
            if mode == "preview"
            else ["--confirm", "offline-placeholder-not-a-real-token"]
        )
        result = cli.run(args)
        assert result.exit_code == 9, result.envelope
        assert result.envelope["error"]["code"] == "E_HUMAN_REQUIRED"
        assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert "data" not in result.envelope
        assert cli._store is None


def test_session_handler_gate_precedes_original_state():
    with pytest.raises(CLIError) as failure:
        S.attach(options())
    assert_blocked(failure.value)


def test_backend_gate_precedes_command_lookup_and_transport(monkeypatch):
    backend = B.NativeBackend()
    monkeypatch.setattr(backend, "_command_path", lambda: pytest.fail("Adapter lookup reached"))
    monkeypatch.setattr(
        B, "_identity_params", lambda *args: pytest.fail("Header injection reached")
    )
    with B.host_bootstrap_ownership({"status": "verified"}), pytest.raises(CLIError) as failure:
        INVOKE(
            backend,
            "designer_host_bootstrap",
            {"domain": "schematic", "native_execution_enabled": True},
        )
    assert_blocked(failure.value)


@pytest.mark.parametrize("entry", ["dispatch", "private-handler"])
def test_adapter_gate_precedes_build_identity_packet_and_com(monkeypatch, entry):
    monkeypatch.setenv("XPEDITION_EXPECTED_ADAPTER_BUILD", "not-a-real-build")
    monkeypatch.setattr(A, "adapter_identity", lambda: pytest.fail("Build inspection reached"))
    function = (
        A.dispatch
        if entry == "dispatch"
        else lambda method, params: A._dispatch_designer_host_bootstrap(params)
    )
    with pytest.raises(A.AdapterError) as failure:
        function("designer_host_bootstrap", {})
    assert_blocked(failure.value)


@pytest.mark.parametrize(
    "entry", ["target", "material", "observe", "private-observe", "revision", "apply", "plan"]
)
def test_legacy_adapter_paths_cannot_reach_old_ownership_or_execution(entry):
    adapter = driver.Adapter()
    call = NS(
        command="session attach",
        params={
            "kind": "schematic",
            "pid": 123,
            "host-bootstrap": "identity",
            "startup-operation-id": "op-offline-original-placeholder",
        },
        effects=("session",),
        dangerous=False,
        dry_run=True,
        operation_id="op-offline-denied",
        plan_id="plan-offline-denied",
        extras={},
    )
    target = legacy_target()
    operations = {
        "target": lambda: adapter._target(call, by_path()["session attach"], options()),
        "material": lambda: adapter._host_bootstrap_material(options()),
        "observe": lambda: adapter._observe(target),
        "private-observe": lambda: adapter._observe_host_bootstrap(target),
        "revision": lambda: adapter.revision(target),
        "apply": lambda: adapter.apply(call, {"target": target}),
        "plan": lambda: adapter.plan(call),
    }
    with pytest.raises(driver.DriverError) as failure:
        operations[entry]()
    assert_blocked(failure.value)


def test_other_write_commands_keep_the_existing_base_path(monkeypatch):
    marker = object()
    monkeypatch.setattr(driver.DriverCLI, "_write", lambda *args: marker)
    monkeypatch.setattr(
        B, "require_designer_host_execution", lambda: pytest.fail("Wrong command gated")
    )
    cli = object.__new__(driver.XpeditionDriverCLI)
    assert cli._write({"path": "session attach"}, {"kind": "pcb"}, {}) is marker
