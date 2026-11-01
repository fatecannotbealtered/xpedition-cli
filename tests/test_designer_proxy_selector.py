from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest
from designer_identity_fakes import Scene, hard_no_native  # noqa: F401

from xpedition_cli import designer_existing_views as E
from xpedition_cli import designer_nativeom as O
from xpedition_cli import designer_proxy_identity as P
from xpedition_cli import designer_window_identity as W
from xpedition_cli import native_com_adapter as A
from xpedition_cli import native_identity as N
from xpedition_cli.backends.native_xpedition import (
    _identity_params,
    identity_ownership,
    selected_designer_identity_probe,
)
from xpedition_cli.cli import sessions
from xpedition_cli.cli.registry import by_path
from xpedition_cli.errors import CLIError

PROBE = "application-proxy"


def test_selector_is_declared_on_existing_read_leaf():
    command = by_path()["session inspect"]
    params = {item.name: item for item in command.params}
    assert params["kind"].choices == ("schematic",)
    assert params["designer-identity-probe"].choices == (PROBE,)
    assert command.tier == "read"


@pytest.mark.parametrize(
    "options",
    [
        {"kind": "schematic"},
        {"designer_identity_probe": PROBE},
        {"kind": "pcb", "designer_identity_probe": PROBE},
        {"kind": "schematic", "designer_identity_probe": "anything-else"},
    ],
)
def test_pair_errors_precede_backend(options):
    with pytest.raises(CLIError) as failure:
        sessions.status(options)
    assert failure.value.code == "E_USAGE"


def test_direct_handler_cannot_silently_fall_back_to_legacy_scans():
    with pytest.raises(CLIError) as failure:
        sessions.status({"kind": "schematic", "designer_identity_probe": PROBE})
    assert failure.value.code == "E_CONFIG"


def fake_status_backend(monkeypatch, *, closing=False):
    calls = []

    class Backend:
        def status(self):
            return {"available": True, "reason": None}

        def invoke(self, method, params, timeout_seconds):
            calls.append((method, _identity_params(method, params), timeout_seconds))
            if method == "health":
                return {"designer_application_running": True, "application_running": True}
            return {"application": {"domain": params["domain"], "document": {"open": True}}}

    monkeypatch.setattr(sessions, "NativeBackend", Backend)
    monkeypatch.setattr(sessions, "recorded_status", lambda: {"state": "attached", "pid": 40})
    monkeypatch.setattr(
        sessions,
        "read_state",
        lambda: {"state": "closing" if closing else "attached", "domain": "schematic", "pid": 40},
    )
    return calls


def test_retired_proxy_optin_refuses_before_health_identity_or_layout(monkeypatch):
    calls = fake_status_backend(monkeypatch)
    owner = {"status": "verified", "authority_granted": False}
    with (
        identity_ownership(lambda: owner, designer_probe=PROBE),
        pytest.raises(CLIError) as failure,
    ):
        sessions.status({"kind": "schematic", "designer_identity_probe": PROBE})
    assert failure.value.code == "E_UNSUPPORTED"
    assert calls == []
    assert selected_designer_identity_probe() is None


def test_default_unbound_dedicated_state_never_discovers_legacy_health_or_rot(monkeypatch):
    calls = fake_status_backend(monkeypatch)
    with identity_ownership(lambda: {"status": "unavailable"}):
        result = sessions.status({})
    assert calls == []
    assert result["designer"]["running"] is None
    assert result["designer"]["identity_error"]["code"] == "E_VALIDATION"
    assert result["layout"]["com_probe_skipped"] == "dedicated Designer owner only"
    assert "identity_probe" not in result


def test_closing_proxy_mode_never_clears_state_or_runs_exit_observation(monkeypatch):
    calls = fake_status_backend(monkeypatch, closing=True)

    def forbidden(*args, **kwargs):
        pytest.fail("diagnostic mode must not clear or observe a pending close")

    monkeypatch.setattr(sessions, "clear_native", forbidden)
    monkeypatch.setattr(sessions, "observe_exit", forbidden)
    with identity_ownership(lambda: {}, designer_probe=PROBE), pytest.raises(CLIError) as failure:
        sessions.status({"kind": "schematic", "designer_identity_probe": PROBE})
    assert failure.value.code == "E_UNSUPPORTED"
    assert calls == []


def test_untrusted_transport_selector_is_stripped_and_scoped_selector_wins():
    params = {"domain": "schematic", "_designer_identity_probe": "forged"}
    assert _identity_params("identity", params) == {"domain": "schematic"}
    with identity_ownership(lambda: {"status": "verified"}, designer_probe=PROBE):
        assert _identity_params("identity", params)["_designer_identity_probe"] == PROBE
        assert "_designer_identity_probe" not in _identity_params("identity", {"domain": "pcb"})
    assert selected_designer_identity_probe() is None


@pytest.mark.parametrize(
    "variable",
    ["XPEDITION_EXPECTED_PID", "XPEDITION_EXPECTED_DOMAIN", "XPEDITION_EXPECTED_SCHEMATIC_PID"],
)
def test_inherited_binding_rejected_before_com_and_all_scans(monkeypatch, variable):
    monkeypatch.setenv(variable, "40")

    def forbidden(*args, **kwargs):
        pytest.fail("must reject before COM or any old window/NativeOM/proxy helper")

    monkeypatch.setattr(A, "_import_com", forbidden)
    monkeypatch.setattr(A, "_viewdraw_active", forbidden)
    monkeypatch.setattr(P, "observe", forbidden)
    with pytest.raises(A.AdapterError) as failure:
        A.dispatch("identity", {"domain": "schematic", "_designer_identity_probe": PROBE})
    assert failure.value.code == "E_CONFLICT"
    assert variable in failure.value.details["present_bound_environment_fields"]


def test_explicit_application_identity_skips_every_old_scan(tmp_path, monkeypatch):
    from xpedition_cli import native_process

    scene = Scene(tmp_path)
    scene.app.ActiveViewHandle = 123  # Even a nonzero scalar cannot route through user32.
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("old window/view/NativeOM branches must not run in proxy mode")

    for module in (W, E, O):
        monkeypatch.setattr(module, "observe", forbidden)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(GetWindowThreadProcessId=forbidden)),
        raising=False,
    )
    monkeypatch.setattr(native_process, "unique_executable_pid", lambda domain, path: {"pid": 40})

    def probe(app, identity, ownership):
        calls.append((app, identity["document"].copy(), ownership))
        return {"status": "observed", "authority_granted": False, "CoDecodeProxy_attempts": 1}

    monkeypatch.setattr(P, "observe", probe)
    owner = {"status": "verified", "target_only": True}
    result = N.application_identity(
        scene.app, "schematic", nativeom_ownership=owner, designer_identity_probe=PROBE
    )
    assert len(calls) == 1 and calls[0][0] is scene.app and calls[0][2] is owner
    assert result["document"]["path"] == scene.path
    assert result["document"]["designs"] == ["PhoneMain"]
    assert result["type_library"]["major"] == 151
    assert result["pid_source"]["property"] == "FullName"
    assert result["designer_application_proxy_observation"]["authority_granted"] is False
    assert (
        not {
            "designer_window_binding",
            "designer_existing_views_observation",
            "designer_nativeom_observation",
        }
        & result.keys()
    )


def test_public_sdk_rejects_retired_proxy_before_any_native_diagnostic(
    tmp_path, monkeypatch, capsys
):
    import json

    from test_driver_v2 import _scope

    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    calls = fake_status_backend(monkeypatch)
    owner = {"status": "verified", "from": "original-record-test", "authority_granted": False}

    def verified(scope):
        assert scope.scope_id == "owned-offline-fixture"
        return owner

    monkeypatch.setattr(driver.Adapter, "_nativeom_ownership", lambda self, scope: verified(scope))
    assert (
        driver.main(
            [
                "session",
                "inspect",
                "--kind",
                "schematic",
                "--designer-identity-probe",
                PROBE,
                "--compact",
            ]
        )
        == 2
    )
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False and envelope["error"]["code"] == "E_UNSUPPORTED"
    assert envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert envelope["error"]["details"]["operation_budget"]["native_call_count"] == 0
    assert calls == []
    assert selected_designer_identity_probe() is None
