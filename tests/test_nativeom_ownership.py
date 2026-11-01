from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from xpedition_cli import driver
from xpedition_cli import nativeom_ownership as own
from xpedition_cli.backends.native_xpedition import (
    NativeBackend,
    _identity_params,
    identity_ownership,
)
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Native/OS discovery is forbidden in ownership-context tests")

    monkeypatch.setattr(NativeBackend, "invoke", forbidden)
    monkeypatch.setattr(NativeBackend, "status", forbidden)
    monkeypatch.setattr(own, "process_identity", forbidden)
    monkeypatch.setattr(own, "_launcher_observation", forbidden)
    yield
    assert hits == []


@pytest.fixture
def case(tmp_path, monkeypatch):
    application = {
        "pid": 40,
        "executable": str(tmp_path / "wv" / "viewdraw.exe"),
        "creation_time_100ns": "123456789",
    }
    launcher = {
        "pid": 41,
        "executable": str(tmp_path / "common" / "viewdraw.exe"),
        "creation_time_100ns": "123456000",
    }
    state = {
        "state": "attached",
        "domain": "schematic",
        "pid": 40,
        "process_identity": copy.deepcopy(application),
    }
    context = {
        "domain": "schematic",
        "process_identity": copy.deepcopy(application),
        "startup_operation_id": "op-original103",
        "startup_provenance": {"tested": "validated by original selector"},
        "requested_project": str(tmp_path / "PHONE4G.prj"),
    }
    pointer = {
        "domain": "schematic",
        "process_identity": copy.deepcopy(application),
        "startup_operation_id": "op-original103",
        "source_sha256": "a" * 64,
    }
    reads, scopes = [], []

    def resolve(domain, actual):
        reads.append((domain, copy.deepcopy(actual)))
        return copy.deepcopy(context), copy.deepcopy(pointer)

    def require(project):
        scopes.append(project)

    def launchers(provenance, actual):
        assert provenance == context["startup_provenance"]
        assert actual == application
        return [copy.deepcopy(launcher)]

    monkeypatch.setattr(own, "owned_startup_launchers", launchers)

    def collect(**updates):
        options = {
            "state_reader": lambda: copy.deepcopy(state),
            "process_reader": lambda domain, pid: copy.deepcopy(application),
            "launcher_reader": lambda expected: {
                "identity": copy.deepcopy(expected),
                "alive": True,
            },
        }
        options.update(updates)
        return own.collect(resolve, require, **options)

    return SimpleNamespace(
        application=application,
        launcher=launcher,
        state=state,
        context=context,
        pointer=pointer,
        reads=reads,
        scopes=scopes,
        collect=collect,
        resolve=resolve,
        require=require,
    )


def test_verified_original_selector_and_scope_supply_exact_generations(case):
    result = case.collect()
    assert result["status"] == "verified"
    assert result["authority_granted"] is False
    assert result["startup_operation_id"] == "op-original103"
    assert result["source_sha256"] == "a" * 64
    assert case.reads == [("schematic", case.application)]
    assert case.scopes == [case.context["requested_project"]]
    assert result["generations"] == [
        {**case.application, "role": "application"},
        {**case.launcher, "role": "launcher"},
    ]
    assert case.state["state"] == "attached"
    assert "startup_provenance" not in result


@pytest.mark.parametrize("field,value", [("domain", "pcb"), ("state", "closing")])
def test_other_or_closing_active_context_supplies_no_candidate(case, field, value):
    case.state[field] = value
    result = case.collect()
    assert result["status"] == "unavailable"
    assert result["generations"] == []
    assert case.reads == []


def test_pid_reuse_is_not_original_generation(case):
    case.state["process_identity"]["creation_time_100ns"] = "1"
    assert case.collect()["reason"] == "active_generation_changed"
    assert case.reads == []


def test_raw_legacy_context_without_registry_pointer_is_not_enough(case):
    result = own.collect(
        lambda *args: (case.context, None),
        case.require,
        state_reader=lambda: case.state,
        process_reader=lambda *args: case.application,
    )
    assert result["reason"] == "original_start_pointer_recovery_required"
    assert result["generations"] == []


@pytest.mark.parametrize("field,value", [("source_sha256", "forged"), ("domain", "pcb")])
def test_pointer_binding_is_not_inferred_from_active_pid(case, field, value):
    case.pointer[field] = value
    assert case.collect()["reason"] == "original_start_context_mismatch"


def test_original_record_or_scope_refusal_is_bounded_and_redacted(case):
    def refused(*args):
        raise CLIError("E_FORBIDDEN", "PRIVATE", {"secret": "PRIVATE"})

    result = own.collect(
        refused,
        case.require,
        state_reader=lambda: case.state,
        process_reader=lambda *args: case.application,
    )
    assert result["reason"] == "original_start_or_scope_validation_failed"
    assert "PRIVATE" not in repr(result)
    assert result["generations"] == []


def test_scope_is_required_before_observing_launcher(case):
    def refused(project):
        raise CLIError("E_FORBIDDEN", "PRIVATE")

    result = own.collect(
        case.resolve,
        refused,
        state_reader=lambda: case.state,
        process_reader=lambda *args: case.application,
    )
    assert result["status"] == "unavailable"
    assert result["generations"] == []


def test_ended_or_reused_launcher_is_not_a_candidate(case):
    result = case.collect(
        launcher_reader=lambda expected: {
            "identity": {**expected, "creation_time_100ns": "999999999"},
            "alive": True,
        }
    )
    assert result["status"] == "verified"
    assert result["generations"] == [{**case.application, "role": "application"}]
    assert result["ended_original_launcher_generations"] == 1


def test_caller_payload_cannot_claim_nativeom_ownership():
    forged = {"domain": "schematic", "_nativeom_ownership": {"status": "verified"}}
    assert _identity_params("identity", forged) == {"domain": "schematic"}
    assert "_nativeom_ownership" in forged  # No caller-data mutation.
    with identity_ownership(lambda: {"status": "verified", "from": "runtime"}):
        assert _identity_params("identity", forged)["_nativeom_ownership"]["from"] == "runtime"
        assert "_nativeom_ownership" not in _identity_params("health", forged)
        assert "_nativeom_ownership" not in _identity_params("identity", {"domain": "pcb"})
    assert "_nativeom_ownership" not in _identity_params("identity", forged)


def test_read_context_resets_after_failure():
    with pytest.raises(RuntimeError), identity_ownership(lambda: {"from": "runtime"}):
        raise RuntimeError("test")
    assert "_nativeom_ownership" not in _identity_params("identity", {"domain": "schematic"})


@pytest.mark.parametrize(
    "command,has_context", [("session inspect", True), ("project inspect", False)]
)
def test_only_public_session_inspect_binds_runtime_ownership(monkeypatch, command, has_context):
    adapter = driver.Adapter(object())
    marker = object()
    seen = []

    def provider(scope):
        assert scope is marker
        seen.append(scope)
        return {"status": "verified", "from": "runtime"}

    def handler(options):
        return {"params": _identity_params("identity", {"domain": "schematic"})}

    monkeypatch.setattr(adapter, "_nativeom_ownership", provider)
    monkeypatch.setattr(driver, "_handler", lambda declared: handler)
    call = SimpleNamespace(
        command=command,
        params={},
        dangerous=False,
        operation_id="op-query",
        extras={"scope": marker},
    )
    result = adapter.run(call)
    assert ("_nativeom_ownership" in result["params"]) is has_context
    assert len(seen) == int(has_context)
    assert "_nativeom_ownership" not in _identity_params("identity", {"domain": "schematic"})
