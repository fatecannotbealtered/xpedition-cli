"""Fixed owner document calls keep adapter guards; no native activation."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_native_project_creation import ProjectApp, _stock

from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


def verified_metadata():
    return {
        "scheme": "designer-fixed-document-call-v1",
        "operation": "preflight",
        "status": "metadata_verified",
        "contracts": {
            name: {"verified": True} for name in ("open_design", "save_all", "save_document")
        },
        "invoke_attempted": False,
        "cleanup": {"status": "observed", "resources_released": True},
    }


def forbidden(*args, **kwargs):
    pytest.fail("The dedicated route must not resolve a default document member")


def test_observed_design_uses_fixed_owner_open_without_default_namespace(monkeypatch):
    app, document, calls = object(), object(), []
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_designer_design_names", lambda value: ["ObservedDesign"])
    monkeypatch.setattr(A, "_designer_documents", forbidden)

    def invoke(value, operation, argument=None):
        assert value is app
        calls.append((operation, argument))
        return document

    monkeypatch.setattr(D, "invoke_document_member", invoke, raising=False)
    assert A._designer_open_design(app, "ObservedDesign") is document
    assert calls == [("open_design", "ObservedDesign")]
    with pytest.raises(A.AdapterError, match="observed native design"):
        A._designer_open_design(app, "UnobservedDesign")
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["save_document", "save_all"])
def test_owned_saves_use_fixed_api_and_never_the_supplied_document(monkeypatch, operation):
    app, calls = object(), []
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(
        D,
        "invoke_document_member",
        lambda value, selected, argument=None: calls.append((value, selected, argument)),
        raising=False,
    )
    if operation == "save_document":
        A._designer_save_document(app, NS(Save=forbidden))
    else:
        A._designer_save_all(app)
    assert calls == [(app, operation, None)]


def test_legacy_saves_keep_original_objects_and_results(monkeypatch):
    calls = []
    document = NS(Save=lambda: calls.append("document") or "legacy_document_result")
    other = NS(Save=lambda: calls.append("explicit") or "legacy_explicit_result")
    app = NS(
        ActiveDocument=document,
        Documents=NS(SaveAll=lambda: calls.append("all") or "legacy_all_result"),
    )
    monkeypatch.setattr(A, "_owner_application", lambda: None)
    monkeypatch.setattr(D, "invoke_document_member", forbidden, raising=False)
    assert A._designer_save_document(app) == "legacy_document_result"
    assert A._designer_save_document(app, other) == "legacy_explicit_result"
    assert A._designer_save_all(app) == "legacy_all_result"
    assert calls == ["document", "explicit", "all"]


@pytest.mark.parametrize("mutation", ["false", "null", "status", "contracts", "cleanup", "invoke"])
def test_bad_preflight_refuses_before_newproject_or_directory_change(
    monkeypatch, tmp_path, mutation
):
    source = _stock(tmp_path)
    target = tmp_path / "new" / "New.prj"
    target.parent.mkdir()
    app, calls = ProjectApp(), []
    metadata = verified_metadata()
    if mutation == "false":
        metadata = False
    elif mutation == "null":
        metadata = None
    elif mutation == "status":
        metadata["status"] = "incomplete"
    elif mutation == "contracts":
        metadata["contracts"] = None
    elif mutation == "cleanup":
        metadata["cleanup"]["resources_released"] = None
    elif mutation == "invoke":
        metadata["invoke_attempted"] = True
    original = source.read_bytes()
    app.NewProject = forbidden
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_viewdraw_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(D, "check_new_project_wrapper", lambda value: calls.append("wrapper"))
    monkeypatch.setattr(
        D,
        "preflight_document_members",
        lambda value: calls.append("preflight") or metadata,
        raising=False,
    )
    monkeypatch.setattr(D, "prepare_new_project_last_error", forbidden)
    with pytest.raises(A.AdapterError) as failure:
        A._clone_project({"project": str(target), "template": str(source)}, object())
    assert failure.value.code == "E_CONFLICT"
    assert failure.value.details["write_attempted"] is False
    assert calls == ["wrapper", "preflight"]
    assert target.parent.is_dir() and not target.exists()
    assert source.read_bytes() == original


def test_verified_preflight_precedes_exact_native_newproject_arguments(monkeypatch, tmp_path):
    source = _stock(tmp_path)
    target = tmp_path / "new" / "New.prj"
    app, calls = ProjectApp(), []
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_viewdraw_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(A, "_designer_prompt_context", nullcontext)
    monkeypatch.setattr(D, "check_new_project_wrapper", lambda value: calls.append("wrapper"))
    monkeypatch.setattr(
        D,
        "preflight_document_members",
        lambda value: calls.append("preflight") or verified_metadata(),
        raising=False,
    )
    monkeypatch.setattr(
        D, "prepare_new_project_last_error", lambda value: calls.append("last_error")
    )

    def new_project(*args):
        calls.append("new_project")
        assert args == (str(target), str(source.parent / "Library/Lib.lmc"), "", str(source))
        raise RuntimeError("fixture stop at native boundary")

    app.NewProject = new_project
    with pytest.raises(RuntimeError, match="fixture stop"):
        A._clone_project({"project": str(target), "template": str(source)}, object())
    assert calls == ["wrapper", "preflight", "last_error", "new_project"]
    assert not target.parent.exists()


@pytest.mark.parametrize("save_fails", [False, True])
def test_owned_close_keeps_path_and_acknowledgement_guards(monkeypatch, tmp_path, save_fails):
    path, calls, state = tmp_path / "own.prj", [], {"open": True}

    def close():
        calls.append("close")
        state["open"] = False
        return True

    app = NS(IsProjectOpened=lambda: state["open"], CloseProject=close)
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_designer_project_path", lambda value: str(path))
    monkeypatch.setattr(A, "_settle", lambda seconds: None)

    def invoke(value, operation, argument=None):
        assert value is app and operation == "save_all" and argument is None
        calls.append("fixed_save_all")
        if save_fails:
            raise RuntimeError("fixture unresolved save")

    monkeypatch.setattr(D, "invoke_document_member", invoke, raising=False)
    lifecycle = {"save_count": 0, "close_count": 0}
    if save_fails:
        with pytest.raises(A.AdapterError):
            A._close_if_open(app, path, lifecycle=lifecycle)
        assert calls == ["fixed_save_all"] and lifecycle["close_count"] == 0
        assert lifecycle.get("save_acknowledged") is not True
    else:
        assert A._close_if_open(app, path, lifecycle=lifecycle) is True
        assert calls == ["fixed_save_all", "close"]
        assert lifecycle["save_acknowledged"] is True and lifecycle["closed_observed"] is True


def test_public_schematic_save_routes_fixed_save_all(monkeypatch):
    app, calls = NS(IsProjectOpened=lambda: True), []
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_viewdraw_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(D, "check_adapter_dispatch", lambda method: True)
    monkeypatch.setattr(
        A, "_import_com", lambda: (NS(CoInitialize=lambda: None, CoUninitialize=lambda: None), NS())
    )
    monkeypatch.setattr(
        D,
        "invoke_document_member",
        lambda value, operation, argument=None: calls.append((value, operation, argument)),
        raising=False,
    )
    assert A.dispatch("save", {"domain": "schematic"}) == {"saved": True, "domain": "schematic"}
    assert calls == [(app, "save_all", None)]


def test_draw_save_routes_fixed_document_save(monkeypatch, tmp_path):
    path, app, calls = tmp_path / "own.prj", object(), []
    path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_viewdraw_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(A, "_prj_lists", lambda *args: True)
    monkeypatch.setattr(A, "_ensure_project", lambda *args: None)
    monkeypatch.setattr(A, "_constants", lambda *args: {"VD_WIRE": 0})
    monkeypatch.setattr(A, "_ensure_sheet", lambda *args: None)
    monkeypatch.setattr(A, "_designer_prompt_context", nullcontext)
    monkeypatch.setattr(A, "_warn_when_the_library_is_missing", lambda *args: None)
    monkeypatch.setattr(A, "_sheet_numbers", lambda *args: {})
    monkeypatch.setattr(
        D,
        "invoke_document_member",
        lambda value, operation, argument=None: calls.append((value, operation, argument)),
        raising=False,
    )
    result = A._draw({"project": str(path), "ops": [{"op": "save"}]}, object())
    assert result["operations"] == {"save": 1}
    assert calls == [(app, "save_document", None)]


def test_cross_sheet_save_uses_fixed_owner_before_switch_or_operation(monkeypatch):
    from xpedition_cli import changeset, schematic_attribute_display, schematic_edit_protection

    app, calls = object(), []
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_designer_snapshot", lambda *args: {})
    monkeypatch.setattr(A, "_verify_designer_reopen_records", lambda *args: {"valid": True})
    monkeypatch.setattr(schematic_attribute_display, "freeze_save_metadata", lambda *args: {})
    monkeypatch.setattr(schematic_attribute_display, "same_save_metadata", lambda *args: True)
    monkeypatch.setattr(
        schematic_edit_protection,
        "validate_supported",
        lambda *args, **kwargs: {"valid": True, "complete": True},
    )
    monkeypatch.setattr(changeset, "apply_operations", lambda *args: ({}, []))
    monkeypatch.setattr(A, "_active_sheet", lambda value: 1)
    monkeypatch.setattr(A, "_ensure_sheet", lambda value, number: calls.append(("sheet", number)))
    monkeypatch.setattr(
        D,
        "invoke_document_member",
        lambda value, operation, argument=None: calls.append((operation, argument)),
        raising=False,
    )

    def operation(*args):
        calls.append(("operation", 2))
        raise A.AdapterError("E_CONFLICT", "fixture stop before an engineering operation")

    monkeypatch.setattr(A, "_apply_designer_operation", operation)
    monkeypatch.setattr(D, "persist_changeset_project", forbidden)
    with pytest.raises(A.AdapterError, match="fixture stop"):
        A._apply_designer_edit_changes(
            app,
            {"project": "own.prj", "operations": [{"type": "move", "sheet": 2}]},
            object(),
            "ObservedDesign",
        )
    assert calls == [("save_document", None), ("sheet", 2), ("operation", 2)]
