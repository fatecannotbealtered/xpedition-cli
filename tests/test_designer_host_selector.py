"""Metadata-only legacy guard regressions with an explicit fake-only gate bypass.

Production denial is tested separately in test_designer_execution_gate.py. The
old daemon/MTA dispatch model is no longer a valid integration expectation.
"""

from __future__ import annotations

import copy
import json
import subprocess
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_driver_v2 import _scope

from xpedition_cli import designer_host_bootstrap as H
from xpedition_cli import designer_startup as D
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
def no_legacy_cad_identity(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("The fixed bootstrap must not enter legacy identity/acquisition scans")

    monkeypatch.setattr(A, "_viewdraw_active", forbidden)
    monkeypatch.setattr(A, "application_identity", forbidden)
    yield
    assert hits == []


@pytest.fixture
def case(monkeypatch, tmp_path):
    # Only injected fake metadata/backend paths below may bypass qualification.
    monkeypatch.setattr(B, "require_designer_host_execution", lambda: None)
    monkeypatch.setattr(D, "require_dedicated_designer", lambda: None)
    _scope(tmp_path, monkeypatch)
    project = tmp_path / "owned/Phone.prj"
    project.parent.mkdir()
    project.write_text("offline fixture")
    actual = {
        "pid": 103,
        "executable": str(tmp_path / "wv/win64/bin/viewdraw.exe"),
        "creation_time_100ns": "134000000000000001",
    }
    pointer = {
        "domain": "schematic",
        "process_identity": copy.deepcopy(actual),
        "startup_operation_id": "op-original-103",
        "source_sha256": "a" * 64,
    }
    O.register(pointer)
    session._write_state(
        {
            "state": "attached",
            "domain": "schematic",
            "pid": 103,
            "startup_operation_id": pointer["startup_operation_id"],
            "process_identity": actual,
        }
    )
    template = {
        "status": "verified",
        "resource": "fixed-fixture.vbs",
        "sha256": "b" * 64,
        "bytes": 80,
        "placeholder_count": 1,
        "engineering_writes": False,
    }
    monkeypatch.setattr(H, "template_identity", lambda: copy.deepcopy(template))
    monkeypatch.setattr(
        driver, "_owned_process_identity", lambda domain, pid: copy.deepcopy(actual)
    )
    rows = [{"pid": actual["pid"], "executable": actual["executable"], "parent_pid": 5}]
    identities, launchers = {actual["pid"]: actual}, []
    monkeypatch.setattr(P, "processes", lambda domain: copy.deepcopy(rows))
    monkeypatch.setattr(P, "process_identity", lambda domain, pid: copy.deepcopy(identities[pid]))
    monkeypatch.setattr(
        P, "owned_startup_launchers", lambda provenance, worker: copy.deepcopy(launchers)
    )
    calls = []

    class Backend:
        def invoke(self, method, params, **kwargs):
            params = B._identity_params(method, params)
            calls.append((method, params))
            assert method == "designer_host_bootstrap"
            assert params["_host_bootstrap_ownership"]["startup_operation_id"] == "op-original-103"
            return {
                "status": "incomplete",
                "authority_granted": False,
                "reason": "fixture_no_native",
                "template": copy.deepcopy(template),
                "runtime_capability_or_path_exported": False,
                "engineering_write_attempted": False,
                "dispatch": {"attempted": True, "returned": True, "result_bool": True},
                "callbacks": {"received": 1, "completed": 1, "accepted": 0, "rejected": 1},
                "cleanup": {
                    key: True
                    for key in (
                        "worker_completed",
                        "closed_to_new_calls",
                        "reference_variables_cleared",
                        "files_removed",
                        "callback_references_quiescent",
                        "mta_uninitialized",
                    )
                }
                | {"inflight_callbacks_at_close": 0},
            }

    backend = Backend()
    monkeypatch.setattr(S, "native", lambda: backend)
    adapter = driver.Adapter(backend)

    def ownership(domain, process, **kwargs):
        assert domain == "schematic" and process == actual
        assert kwargs.get("source_id") == "op-original-103"
        return {"requested_project": str(project)}, copy.deepcopy(pointer)

    monkeypatch.setattr(adapter, "_ownership", ownership)
    options = {
        "host_bootstrap": "identity",
        "kind": "schematic",
        "pid": 103,
        "startup_operation_id": "op-original-103",
    }
    return NS(
        adapter=adapter,
        calls=calls,
        options=options,
        pointer=pointer,
        actual=actual,
        project=project,
        template=template,
        helper=H,
        rows=rows,
        identities=identities,
        launchers=launchers,
        state_bytes=session._state_path().read_bytes(),
        registry_bytes=O.registry_path().read_bytes(),
    )


def test_existing_attach_leaf_declares_only_fixed_identity_choice():
    command = by_path()["session attach"]
    assert next(p for p in command.params if p.name == "host-bootstrap").choices == ("identity",)
    assert command.extra["effects"] == ["session", "persist"] and command.tier == "write"
    assert len(by_path()) == 62  # The SDK adds its three built-in public leaves.


@pytest.mark.parametrize("change", ["kind", "mode", "source", "project", "pid"])
def test_widened_options_refuse_before_native(case, change):
    options = {**case.options}
    options.update(
        {"kind": "pcb"}
        if change == "kind"
        else {"host_bootstrap": "run-arbitrary"}
        if change == "mode"
        else {"startup_operation_id": None}
        if change == "source"
        else {"project": str(case.project)}
        if change == "project"
        else {"pid": 0}
    )
    with pytest.raises(CLIError):
        S.attach(options)
    assert case.calls == []


def test_direct_handler_cannot_create_trusted_context(case):
    with pytest.raises(CLIError) as error:
        S.attach(case.options)
    assert error.value.code == "E_CONFIG" and case.calls == []


def test_actual_helper_accepts_the_sdk_producer_header(case):
    packet, _, _ = case.adapter._host_bootstrap_material(
        case.options, NS(scope_id="owned-offline-fixture", require=lambda **kwargs: None)
    )
    worker, project = H._header(packet, 103)
    assert worker == {**case.actual, "role": "application"}
    assert project == H.W._path(str(case.project))
    packet["verification_basis"] = "unverified-copy"
    with pytest.raises(H.BootstrapGap):
        H._header(packet, 103)


@pytest.mark.parametrize(
    "problem", ["missing_worker", "duplicate", "unowned", "unreadable", "over_bound"]
)
def test_complete_native_census_refuses_before_any_candidate(case, problem):
    if problem == "missing_worker":
        case.rows.clear()
    elif problem == "duplicate":
        case.rows.append(copy.deepcopy(case.rows[0]))
    elif problem == "unowned":
        case.identities[999] = {**case.actual, "pid": 999}
        case.rows.append({"pid": 999, "parent_pid": 1, "executable": case.actual["executable"]})
    elif problem == "unreadable":
        case.identities.clear()
    else:
        case.rows *= 65
    with pytest.raises(CLIError) as error:
        case.adapter._host_bootstrap_material(
            case.options, NS(scope_id="owned-offline-fixture", require=lambda **kwargs: None)
        )
    assert error.value.code == "E_CONFLICT"
    assert error.value.details["reason"] == "host_bootstrap_native_census_unverified"
    assert case.calls == []


def test_proven_live_launcher_is_bound_with_the_worker(case):
    launcher = {
        "pid": 102,
        "executable": str(case.project.parent / "common/win64/bin/viewdraw.exe"),
        "creation_time_100ns": "134000000000000000",
    }
    case.launchers.append(launcher)
    case.identities[102] = launcher
    case.rows.append({"pid": 102, "parent_pid": 1, "executable": launcher["executable"]})
    packet, _, _ = case.adapter._host_bootstrap_material(
        case.options, NS(scope_id="owned-offline-fixture", require=lambda **kwargs: None)
    )
    assert packet["generations"] == [
        {**case.actual, "role": "application"},
        {**launcher, "role": "launcher"},
    ]
    assert H._header(packet, 103)[0]["pid"] == 103


def test_caller_packet_is_removed_and_context_is_method_limited():
    params = {"domain": "schematic", "_host_bootstrap_ownership": {"forged": True}}
    assert B._identity_params("designer_host_bootstrap", params) == {"domain": "schematic"}
    with B.host_bootstrap_ownership({"verified": True}):
        assert B._identity_params("designer_host_bootstrap", params)[
            "_host_bootstrap_ownership"
        ] == {"verified": True}
        assert "_host_bootstrap_ownership" not in B._identity_params("identity", params)
        assert "_host_bootstrap_ownership" not in B._identity_params(
            "designer_host_bootstrap", {"domain": "pcb"}
        )
    assert B.selected_host_bootstrap_ownership() is None


def test_public_preview_has_no_native_and_confirm_one_experiment(case, tmp_path):
    args = [
        "session",
        "attach",
        "--kind",
        "schematic",
        "--pid",
        "103",
        "--startup-operation-id",
        "op-original-103",
        "--host-bootstrap",
        "identity",
    ]
    with driver.runtime(case.adapter, state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        assert case.calls == []
        plan = preview.envelope["data"]["preview"]
        assert plan["effects"] == ["session"]
        assert "objref:" not in json.dumps(preview.envelope).lower()
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"], applied.envelope
        result = applied.envelope["data"]["receipt"]["extensions"]["result"]
        assert result["authority_granted"] is False and result["attached"] is False
        assert result["host_bootstrap"]["status"] == "incomplete"
        assert len(case.calls) == 1 and case.calls[0][0] == "designer_host_bootstrap"
    assert session._state_path().read_bytes() == case.state_bytes
    assert O.registry_path().read_bytes() == case.registry_bytes
    assert B.selected_host_bootstrap_ownership() is None


@pytest.mark.parametrize("drift", ["generation", "pointer", "template", "state", "unowned"])
def test_preview_to_confirm_drift_prevents_dispatch(case, tmp_path, monkeypatch, drift):
    args = [
        "session",
        "attach",
        "--kind",
        "schematic",
        "--pid",
        "103",
        "--startup-operation-id",
        "op-original-103",
        "--host-bootstrap",
        "identity",
    ]
    with driver.runtime(case.adapter, state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        if drift == "generation":
            case.actual["creation_time_100ns"] = "134000000000000002"
        elif drift == "pointer":
            O.registry_path().unlink()
        elif drift == "template":
            case.template["sha256"] = "c" * 64
        elif drift == "state":
            session._write_state({"state": "closing"})
        else:
            case.identities[999] = {**case.actual, "pid": 999}
            case.rows.append({"pid": 999, "parent_pid": 1, "executable": case.actual["executable"]})
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"] is False
    assert case.calls == []


def test_adapter_invalid_packet_precedes_binding_import(monkeypatch):
    monkeypatch.setattr(A, "_import_com", lambda: pytest.fail("COM import reached"))
    with pytest.raises(A.AdapterError) as error:
        A.dispatch("designer_host_bootstrap", {"domain": "schematic"})
    assert error.value.code == "E_HUMAN_REQUIRED"
    assert error.value.details["write_attempted"] is False


@pytest.mark.parametrize(
    "name",
    [
        "XPEDITION_EXPECTED_PID",
        "XPEDITION_EXPECTED_DOMAIN",
        "XPEDITION_EXPECTED_SCHEMATIC_PID",
        "XPEDITION_OBSERVE_ONLY",
    ],
)
def test_inherited_binding_or_query_mode_cannot_enter_bootstrap(case, monkeypatch, name):
    packet, _, _ = case.adapter._host_bootstrap_material(
        case.options, NS(scope_id="owned-offline-fixture", require=lambda **kwargs: None)
    )
    monkeypatch.setenv(name, "1")
    monkeypatch.setattr(A, "_import_com", lambda: pytest.fail("COM import reached"))
    with pytest.raises(A.AdapterError) as error:
        A.dispatch(
            "designer_host_bootstrap", {"domain": "schematic", "_host_bootstrap_ownership": packet}
        )
    assert error.value.code == "E_CONFLICT" and error.value.details["write_attempted"] is False


def test_changed_session_after_dispatch_cannot_pass_diagnostic_verification(
    case, tmp_path, monkeypatch
):
    original = S.native().invoke

    def changed(method, params, **kwargs):
        result = original(method, params, **kwargs)
        session._write_state({"state": "attached", "unexpected": "changed by fake helper"})
        return result

    monkeypatch.setattr(S.native(), "invoke", changed)
    args = [
        "session",
        "attach",
        "--kind",
        "schematic",
        "--pid",
        "103",
        "--startup-operation-id",
        "op-original-103",
        "--host-bootstrap",
        "identity",
    ]
    with driver.runtime(case.adapter, state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"] is False
        assert applied.envelope["error"]["code"] == "E_VERIFY_INCOMPLETE"
    assert len(case.calls) == 1


@pytest.mark.parametrize("pending_kind", ["worker", "callback_absent", "inflight"])
def test_pending_bootstrap_retains_new_unknown_without_touching_original_state(
    case, tmp_path, monkeypatch, pending_kind
):
    original = S.native().invoke

    def pending(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if pending_kind == "worker":
            result["cleanup"]["worker_completed"] = False
            result["reason"] = "bootstrap_worker_pending_after_total_budget"
        elif pending_kind == "callback_absent":
            result["callbacks"] = {"received": 0, "completed": 0, "accepted": 0, "rejected": 0}
            result["reason"] = "bootstrap_host_callback_not_completed"
        else:
            result["cleanup"].update(
                callback_references_quiescent=False,
                inflight_callbacks_at_close=1,
                reference_variables_cleared=False,
                mta_uninitialized=False,
            )
            result["reason"] = "bootstrap_callback_cleanup_pending"
        return result

    monkeypatch.setattr(S.native(), "invoke", pending)
    args = [
        "session",
        "attach",
        "--kind",
        "schematic",
        "--pid",
        "103",
        "--startup-operation-id",
        "op-original-103",
        "--host-bootstrap",
        "identity",
    ]
    with driver.runtime(case.adapter, state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"] is False, applied.envelope
        assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", applied.envelope
        operation_id = applied.envelope["error"]["details"]["operation_id"]
        retained = cli._store_open().get(operation_id)
        assert retained["status"] == "unknown" and retained["resolution"] is None
    assert session._state_path().read_bytes() == case.state_bytes
    assert O.registry_path().read_bytes() == case.registry_bytes
    assert len(case.calls) == 1


def test_bootstrap_timeout_preserves_original_session(case, tmp_path, monkeypatch):
    binary = tmp_path / "fake-adapter.exe"
    binary.write_bytes(b"not executable; subprocess is explicitly fake")
    backend = B.NativeBackend()
    monkeypatch.setattr(backend, "_command_path", lambda: binary)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fake-only", 1)

    monkeypatch.setattr(B.subprocess, "run", timeout)
    with B.host_bootstrap_ownership({"target": "fake"}), pytest.raises(CLIError) as error:
        INVOKE(backend, "designer_host_bootstrap", {"domain": "schematic"}, timeout_seconds=1)
    assert error.value.code == "E_TIMEOUT"
    assert error.value.details["write_attempted"] is True
    assert session._state_path().read_bytes() == case.state_bytes


@pytest.mark.parametrize(
    "field,new_value,frozen,allowed",
    [
        ("module_path", "C:/private/_MEIconfirm/xpedition_cli/native_com_adapter.py", True, True),
        ("build_sha256", "f" * 64, True, False),
        ("python_executable", "D:/foreign/xpedition-cli.exe", True, False),
        ("module_sha256", "f" * 64, True, False),
        ("module_path", "D:/other/native_com_adapter.py", False, False),
    ],
)
def test_frozen_extraction_path_is_separate_from_build_and_source_bindings(
    case, tmp_path, monkeypatch, field, new_value, frozen, allowed
):
    identity = {
        "version": "2.0.0-dev.1",
        "identity_basis": "frozen_executable_bytes" if frozen else "loaded_source_bytes",
        "build_sha256": "c" * 64,
        "module_sha256": "d" * 64,
        "helper_sha256": "e" * 64,
        "python_executable": "D:/candidate/bin/xpedition-cli.exe",
        "client_bitness": 64,
        "module_path": "C:/private/_MEIpreview/xpedition_cli/native_com_adapter.py",
    }
    original = copy.deepcopy(identity)
    monkeypatch.setattr(driver, "adapter_identity", lambda: copy.deepcopy(identity))
    args = [
        "session",
        "attach",
        "--kind",
        "schematic",
        "--pid",
        "103",
        "--startup-operation-id",
        "op-original-103",
        "--host-bootstrap",
        "identity",
    ]
    with driver.runtime(case.adapter, state_dir=tmp_path / "sdk") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        identity[field] = new_value
        applied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert applied.envelope["ok"] is allowed, applied.envelope
        if allowed:
            receipt = applied.envelope["data"]["receipt"]
            stored = cli._store_open().get(receipt["operation_id"])
            raw_before = stored["plan"]["base_snapshot"]["extensions"]["facts"]["adapter"]
            raw_after = receipt["snapshot_after"]["extensions"]["facts"]["adapter"]
            assert raw_before == original and raw_after == identity
            assert raw_before["module_path"] != raw_after["module_path"]
            assert len(case.calls) == 1
        else:
            assert applied.envelope["error"]["code"] == "E_CONFLICT"
            assert applied.envelope["error"]["details"]["dispatch_state"] == "not_started"
            assert case.calls == []
    assert session._state_path().read_bytes() == case.state_bytes
    assert O.registry_path().read_bytes() == case.registry_bytes
