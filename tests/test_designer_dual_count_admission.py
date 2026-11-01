"""Pending bridge cleanup forbids further writes without hiding pure records."""

from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_designer_owner_dispatch import scene as scene  # noqa: F401

from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


@pytest.mark.parametrize("kind", ["count", "document"])
def test_sticky_count_cleanup_blocks_all_engineering_and_connection_writes(
    scene, monkeypatch, kind
):
    field = "_dual_count_probe" if kind == "count" else "_dual_document_call"
    setattr(scene.owner, field, NS(resources_released=False))
    calls = []
    monkeypatch.setattr(
        A, "dispatch", lambda method, params: calls.append(method) or {"method": method}
    )
    for method in D.WRITE_METHODS:
        with pytest.raises(D.DispatchError, match="cleanup_pending") as rejected:
            D.dispatch(scene.grant, method, {})
        assert rejected.value.dispatch_started is False
    assert calls == []
    for method, params in (
        ("identity", {}),
        ("read_project", {}),
        ("snapshot", {"observe_only": True}),
    ):
        D.dispatch(scene.grant, method, params)
    assert calls == ["identity", "read_project", "snapshot"]
    assert getattr(scene.owner, field).resources_released is False


def test_fixed_document_api_requires_current_opaque_owner_and_never_uses_flags(scene):
    with pytest.raises(D.DispatchError, match="current_authorized_write"):
        D.invoke_document_member(scene.app, "save_all")
    with pytest.raises(D.DispatchError, match="current_authorized_write"):
        D.preflight_document_members(scene.app)


def test_service_close_authorization_rejects_before_native_quit_or_new_scope_effects(monkeypatch):
    required = []
    monkeypatch.setattr(
        D,
        "validate_editing_scope",
        lambda value: NS(
            scope_id="scope-current", require=lambda **kwargs: required.append(kwargs)
        ),
    )
    owner = NS(_dual_count_probe=NS(resources_released=False), readonly_quarantine=None)
    scope = {"scope_sha256": "a" * 64}
    binding = {
        "command": "session close",
        "scope_id": "scope-current",
        "scope_sha256": "a" * 64,
        "source_operation_id": "op-current-close",
    }
    with pytest.raises(D.DispatchError, match="dual_count_cleanup_pending") as rejected:
        D.authorize(owner, scope, "close", {}, "op-current-close", binding)
    assert rejected.value.dispatch_started is False
    assert required == []


@pytest.mark.parametrize("state", [None, True])
def test_active_probe_none_and_observed_release_are_not_treated_as_failed_cleanup(state):
    owner = NS(_dual_count_probe=NS(resources_released=state), readonly_quarantine=None)
    for method in D.WRITE_METHODS:
        D.require_service_method(owner, method, {})


@pytest.mark.parametrize(
    "method,arguments",
    [
        ("quit_empty", ()),
        ("save_close_project", ()),
        ("require_reopen_allowed", ()),
        ("confirm_project_open", ()),
        ("configure_silent_mode", ()),
        ("run_pdf_producer", ([], "unused")),
    ],
)
def test_direct_owner_lifecycle_methods_reject_before_calling_native(scene, method, arguments):
    scene.owner._dual_count_probe = NS(resources_released=False)
    before = list(scene.calls)
    with pytest.raises(O.OwnerError, match="native_dual_count_cleanup_pending"):
        getattr(scene.owner, method)(*arguments)
    assert scene.calls == before


def test_local_type_library_authorization_uses_original_signed_owner_scope(
    scene, monkeypatch, tmp_path
):
    scope_input = {"original": "signed_scope_binding"}
    calls = []
    scene.owner.runtime_root = tmp_path / "owner-runtime"

    def context(ticket):
        assert ticket.root == scene.owner.runtime_root
        assert ticket.epoch == scene.handoff._epoch
        return ticket.root, object(), {"mode": O.PUBLIC_MODE, "editing_scope": scope_input}

    def validate(value):
        assert value is scope_input
        return NS(require=lambda **kwargs: calls.append(kwargs))

    monkeypatch.setattr(O, "_context", context)
    monkeypatch.setattr(D, "validate_editing_scope", validate)
    path = tmp_path / "installed" / "viewdraw.tlb"
    scene.owner._authorize_dual_typelib_source(path)
    assert calls == [{"effects": ["observe"], "paths": [(str(path), "observe")]}]


@pytest.mark.parametrize("fault", ["missing_runtime", "nonpublic", "invalid_signature"])
def test_missing_or_unsigned_source_context_cannot_authorize_dependency(
    scene, monkeypatch, tmp_path, fault
):
    scene.owner.runtime_root = None if fault == "missing_runtime" else tmp_path / "owner-runtime"

    def context(ticket):
        if fault == "invalid_signature":
            raise O.OwnerError("fixed_invalid_original_signature")
        return ticket.root, object(), {"mode": "measurement-only", "editing_scope": {}}

    monkeypatch.setattr(O, "_context", context)
    monkeypatch.setattr(
        D, "validate_editing_scope", lambda *args: pytest.fail("No original public signed scope")
    )
    with pytest.raises(O.OwnerError):
        scene.owner._authorize_dual_typelib_source(tmp_path / "viewdraw.tlb")
