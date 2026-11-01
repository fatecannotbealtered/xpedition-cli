"""Reject legacy takeover paths; fresh empty startup uses the scoped SDK route."""

from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import driver, session
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_process as P
from xpedition_cli import session_ownership as O
from xpedition_cli.backends import native_xpedition as B
from xpedition_cli.cli import sessions as S
from xpedition_cli.cli.registry import by_path
from xpedition_cli.errors import CLIError

INVOKE = B.NativeBackend.invoke
FACTORY = A._viewdraw_application


@pytest.fixture(autouse=True)
def no_original_or_native_resources(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Dedicated-startup refusal reached original state, OS, COM or a worker")

    for owner, name in (
        (S, "_require_no_pending_close"),
        (S, "read_state"),
        (S, "_project_for"),
        (session, "read_state"),
        (session, "clear_native"),
        (O, "pointer_for"),
        (O, "require_registration_capacity"),
        (driver, "_owned_process_identity"),
        (driver, "adapter_identity"),
        (P, "_startup_snapshot"),
        (P, "process_identity"),
        (A, "_import_com"),
        (A, "_viewdraw_active"),
        (A, "_viewdraw_application"),
        (A, "adapter_identity"),
        (A, "_native_executable"),
        (A, "_configure_environment"),
        (threading, "Thread"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    yield
    assert hits == [], "Swallowing a state/native lookup still fails the gate test"


def assert_denied(error):
    assert error.code == "E_HUMAN_REQUIRED"
    assert error.details["write_attempted"] is False
    assert error.details["state_write_attempted"] is False
    assert error.details["native_request_dispatched"] is False
    assert error.details["native_execution_enabled"] is False
    readiness = error.details["startup_readiness"]
    assert readiness["arbitrary_running_instance_takeover"] == "unsupported"
    assert readiness["native_connection_qualified"] is False
    assert readiness["runtime_ExecuteCommand_Run"] == "not_used"


@pytest.mark.parametrize("command", ["start", "attach"])
@pytest.mark.parametrize("mode", ["preview", "old-confirm"])
def test_attach_and_project_start_refuse_before_scope_store_or_token(
    tmp_path, monkeypatch, command, mode
):
    adapter = driver.Adapter()
    with driver.runtime(adapter, state_dir=tmp_path / "new-sdk-fixture") as cli:

        def forbidden(*args, **kwargs):
            pytest.fail("The disabled Designer entry must not touch scope, store or plans")

        for owner, name in (
            (cli, "_scope"),
            (cli, "_store_open"),
            (adapter, "plan"),
            (adapter, "revision"),
            (adapter, "apply"),
        ):
            monkeypatch.setattr(owner, name, forbidden)
        args = ["session", command, "--kind", "schematic"]
        if command == "attach":
            args += ["--pid", "123", "--startup-operation-id", "op-old-placeholder"]
        else:
            args += ["--project", "never-read-old-project.prj"]
        args += ["--dry-run"] if mode == "preview" else ["--confirm", "old-placeholder"]
        reply = cli.run(args)
        assert reply.exit_code == 9, reply.envelope
        assert reply.envelope["error"]["code"] == "E_HUMAN_REQUIRED"
        assert reply.envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert "data" not in reply.envelope
        assert cli._store is None


@pytest.mark.parametrize("mode", ["preview", "old-confirm"])
def test_empty_start_requires_real_scope_before_store_plan_or_native(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(tmp_path / "missing-new-scope.json"))
    adapter = driver.Adapter()
    with driver.runtime(adapter, state_dir=tmp_path / "new-sdk-fixture") as cli:

        def forbidden(*args, **kwargs):
            pytest.fail("An ungranted empty startup reached the store or adapter")

        for owner, name in (
            (cli, "_store_open"),
            (adapter, "plan"),
            (adapter, "revision"),
            (adapter, "apply"),
        ):
            monkeypatch.setattr(owner, name, forbidden)
        args = ["session", "start", "--kind", "schematic"]
        args += ["--dry-run"] if mode == "preview" else ["--confirm", "old-placeholder"]
        reply = cli.run(args)
        assert reply.envelope["error"]["code"] == "E_FORBIDDEN", reply.envelope
        assert reply.envelope["error"]["details"]["dispatch_state"] == "not_started"
        assert cli._store is None


def test_empty_start_still_requires_sdk_confirmation_before_scope_or_dispatch(
    tmp_path, monkeypatch
):
    adapter = driver.Adapter()
    with driver.runtime(adapter, state_dir=tmp_path / "new-sdk-fixture") as cli:

        def forbidden(*args, **kwargs):
            pytest.fail("An unconfirmed empty startup reached scope or dispatch")

        monkeypatch.setattr(cli, "_scope", forbidden)
        monkeypatch.setattr(cli, "_store_open", forbidden)
        monkeypatch.setattr(adapter, "apply", forbidden)
        reply = cli.run(["session", "start", "--kind", "schematic"])
        assert reply.envelope["error"]["code"] == "E_CONFIRMATION_REQUIRED", reply.envelope
        assert cli._store is None


@pytest.mark.parametrize("command", ["start", "attach"])
def test_direct_session_handlers_refuse_before_original_state(command):
    with pytest.raises(CLIError) as failure:
        getattr(S, command)({"kind": "schematic", "pid": 123, "project": "never-read.prj"})
    if command == "start":
        assert failure.value.code == "E_UNSUPPORTED"
        assert failure.value.details["write_attempted"] is False
    else:
        assert_denied(failure.value)


@pytest.mark.parametrize("method", ["start", "attach"])
@pytest.mark.parametrize("selection", [{"domain": "schematic"}, {"kind": "designer"}])
def test_backend_and_adapter_direct_entries_cannot_fall_back(monkeypatch, method, selection):
    backend = B.NativeBackend()
    monkeypatch.setattr(backend, "_command_path", lambda: pytest.fail("Transport lookup reached"))
    with pytest.raises(CLIError) as failure:
        INVOKE(backend, method, selection)
    assert_denied(failure.value)
    with pytest.raises(A.AdapterError) as failure:
        A.dispatch(method, selection)
    assert_denied(failure.value)


@pytest.mark.parametrize("method", ["start", "attach"])
@pytest.mark.parametrize("selection", [{}, {"project": "ambiguous.prj"}])
def test_direct_native_entry_requires_explicit_domain_before_io(monkeypatch, method, selection):
    backend = B.NativeBackend()
    monkeypatch.setattr(backend, "_command_path", lambda: pytest.fail("Transport lookup reached"))
    with pytest.raises(CLIError) as failure:
        INVOKE(backend, method, selection)
    assert failure.value.code == "E_USAGE"
    assert failure.value.details["native_request_dispatched"] is False
    with pytest.raises(A.AdapterError) as failure:
        A.dispatch(method, selection)
    assert failure.value.code == "E_USAGE"
    assert failure.value.details["native_request_dispatched"] is False


@pytest.mark.parametrize("method", ["start", "attach", "open"])
def test_explicit_pcb_prj_keeps_existing_native_selection(monkeypatch, method):
    class ExistingPCBRoute(Exception):
        pass

    def stop_at_existing_route(*args, **kwargs):
        raise ExistingPCBRoute

    backend = B.NativeBackend()
    monkeypatch.setattr(backend, "_command_path", stop_at_existing_route)
    monkeypatch.setattr(A, "_import_com", stop_at_existing_route)
    params = {"domain": "pcb", "project": "layout-project.prj"}
    with pytest.raises(ExistingPCBRoute):
        INVOKE(backend, method, params)
    with pytest.raises(ExistingPCBRoute):
        A.dispatch(method, params)


def test_private_launcher_cannot_start_designer():
    with pytest.raises(A.AdapterError) as failure:
        A._start_by_launcher(object(), "schematic")
    assert_denied(failure.value)


@pytest.mark.parametrize("attach_only", [False, True])
def test_legacy_designer_factory_refuses_before_rot_or_dispatch(attach_only):
    def forbidden(*args, **kwargs):
        pytest.fail("The disabled factory must not access ROT or Dispatch")

    client = NS(GetActiveObject=forbidden, Dispatch=forbidden)
    with pytest.raises(A.AdapterError) as failure:
        FACTORY(client, attach_only=attach_only)
    assert_denied(failure.value)


@pytest.mark.parametrize("start", [False, True])
def test_indirect_schematic_open_cannot_acquire_or_create_an_application(monkeypatch, start):
    backend = B.NativeBackend()
    monkeypatch.setattr(backend, "_command_path", lambda: pytest.fail("Transport lookup reached"))
    monkeypatch.setattr(B, "_identity_params", lambda *a: pytest.fail("Header injection reached"))
    params = {"domain": "schematic", "project": "owned.prj", "start": start}
    with pytest.raises(CLIError) as failure:
        INVOKE(backend, "open", params)
    assert_denied(failure.value)
    with pytest.raises(A.AdapterError) as failure:
        A.dispatch("open", params)
    assert_denied(failure.value)


@pytest.mark.parametrize("command", ["session start", "session attach"])
@pytest.mark.parametrize("entry", ["target", "plan", "observe", "revision", "apply"])
def test_legacy_plans_refuse_before_original_metadata_or_identity(command, entry, monkeypatch):
    adapter = driver.Adapter()
    monkeypatch.setattr(adapter, "_identity", lambda *a: pytest.fail("Identity lookup reached"))
    monkeypatch.setattr(
        adapter, "_ownership_options", lambda *a: pytest.fail("Ownership preparation reached")
    )
    call = NS(
        command=command,
        params={
            "kind": "schematic",
            **(
                {"project": "never-read-old-project.prj"}
                if command == "session start" and entry in {"target", "plan"}
                else {}
            ),
        },
        effects=("session",),
        dangerous=False,
        dry_run=True,
        operation_id="op-offline-fixture",
        plan_id="plan-offline-fixture",
        extras={},
    )
    target = {
        "mode": "file" if command == "session start" else "session",
        "engine_id": "xpedition-cli-files" if command == "session start" else "xpedition-schematic",
        "extensions": {"command": command, "domain": "schematic"},
    }
    operations = {
        "target": lambda: adapter._target(call, by_path()[command], dict(call.params)),
        "plan": lambda: adapter.plan(call),
        "observe": lambda: adapter._observe(target),
        "revision": lambda: adapter.revision(target),
        "apply": lambda: adapter.apply(call, {"target": target}),
    }
    with pytest.raises(driver.DriverError) as failure:
        operations[entry]()
    assert_denied(failure.value)
    assert failure.value.dispatch_state == "not_started"


@pytest.mark.parametrize("source", ["call-kind", "target-domain", "target-engine"])
def test_legacy_attach_cannot_hide_schematic_selection_in_another_binding(source):
    call = NS(
        command="session attach",
        params={"kind": "schematic" if source == "call-kind" else "pcb"},
        operation_id="op-offline-fixture",
    )
    target = {"extensions": {"command": "session attach"}}
    if source == "target-domain":
        target["extensions"]["domain"] = "schematic"
    if source == "target-engine":
        target["engine_id"] = "xpedition-schematic"
    with pytest.raises(driver.DriverError) as failure:
        driver.Adapter().apply(call, {"target": target})
    assert_denied(failure.value)


def test_pcb_write_keeps_the_existing_sdk_route(monkeypatch):
    marker = object()
    monkeypatch.setattr(driver.DriverCLI, "_write", lambda *a: marker)
    monkeypatch.setattr(
        driver, "_require_dedicated_designer", lambda: pytest.fail("PCB incorrectly gated")
    )
    cli = object.__new__(driver.XpeditionDriverCLI)
    for command in ("session start", "session attach", "schematic draw"):
        assert cli._write({"path": command}, {"kind": "pcb"}, {}) is marker
