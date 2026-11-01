"""Regression for the documented method on an IDispatch without live typeinfo."""

import pytest


@pytest.mark.parametrize("verify", [False, True])
def test_in_synch_save_flag_matches_the_executed_save_reopen(tmp_path, monkeypatch, verify):
    from types import SimpleNamespace

    from xpedition_cli import native_com_adapter as a

    board = tmp_path / "PCB/QA.pcb"
    integration = SimpleNamespace(ForwardAnnotationStatus=a.PRJINT_IN_SYNCH)
    doc = SimpleNamespace(ProjectIntegration=integration)
    events = []
    monkeypatch.setattr(a, "_layout_board_path", lambda p: board)
    monkeypatch.setattr(a, "_close_board_if_open", lambda *x: False)
    monkeypatch.setattr(a, "_application", lambda *x, **k: object())
    monkeypatch.setattr(a, "_open_layout_document", lambda *x: (doc, []))
    monkeypatch.setattr(a, "_licensed_document", lambda *x: doc)
    monkeypatch.setattr(a, "_reopen_board", lambda *x: events.append("save_close_open") or doc)
    monkeypatch.setattr(a, "_board_counts", lambda *x: {"components": 2, "nets": 2})
    result = a._forward_annotate({"verify": verify}, object())
    assert result["outcome"] == "in_synch"
    assert result["saved"] is verify
    assert result["persistence_verified"] is verify
    assert events == (["save_close_open"] if verify else [])
    assert result["log_status"] == "not_generated"
    assert result["log"] is None
    assert result["components_found"] is None


def test_forward_annotation_uses_method_dispatch_without_property_probe():
    from xpedition_cli import native_com_adapter as adapter

    calls = []

    class NativeDispatch:
        def GetIDsOfNames(self, name):
            assert name == "ForwardAnnotate"
            return 2

        def InvokeTypes(self, member, lcid, kind, result_type, args):
            calls.append((member, kind, result_type, args))
            return True

    class IntegrationWithoutTypeInfo:
        _oleobj_ = NativeDispatch()

        @property
        def ForwardAnnotate(self):
            raise AssertionError("A property probe can execute under the native database lock")

    assert adapter._invoke_forward_annotation(IntegrationWithoutTypeInfo()) is True
    assert calls == [(2, 1, (11, 0), ())]


def test_forward_annotation_does_not_retry_after_native_method_failure():
    from types import SimpleNamespace

    import pytest

    from xpedition_cli import native_com_adapter as adapter

    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError("synthetic failure after dispatch")

    integration = SimpleNamespace(
        _oleobj_=SimpleNamespace(GetIDsOfNames=lambda name: 2, InvokeTypes=fail)
    )
    with pytest.raises(RuntimeError, match="after dispatch"):
        adapter._invoke_forward_annotation(integration)
    assert len(calls) == 1


@pytest.mark.parametrize("restore_fails", [False, True])
def test_annotation_saves_and_reopens_layout_before_restoring_designer(
    tmp_path, monkeypatch, restore_fails
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from xpedition_cli import native_com_adapter as a

    project = tmp_path / "CONTROL.prj"
    project.write_text("synthetic initialized project", encoding="utf-8")
    board = tmp_path / "PCB/CONTROL.pcb"
    events = []
    integration = SimpleNamespace(
        ForwardAnnotationStatus=0,
        IsForwardAnnotationAllowed=True,
        ForwardAnnotate=lambda: events.append("annotate") or True,
    )
    doc = SimpleNamespace(ProjectIntegration=integration, Save=lambda: events.append("save_layout"))
    app, designer = object(), object()
    monkeypatch.setattr(a, "_layout_board_path", lambda p: board)
    monkeypatch.setattr(a, "_close_board_if_open", lambda *x: True)
    monkeypatch.setattr(a, "_application", lambda *x, **k: app)
    monkeypatch.setattr(a, "_open_layout_document", lambda *x: (doc, []))
    monkeypatch.setattr(a, "_licensed_document", lambda *x: doc)
    monkeypatch.setattr(a, "_release_project", lambda *x: (designer, True))

    def restore(*args):
        events.append("restore_designer")
        if restore_fails:
            raise a.AdapterError("E_NOT_FOUND", "synthetic Designer restore failure")

    monkeypatch.setattr(a, "_ensure_project", restore)
    monkeypatch.setattr(a, "_reopen_board", lambda *x: events.append("reopen_layout") or doc)
    monkeypatch.setattr(a, "_settle", lambda *x: None)
    monkeypatch.setattr(a, "_routing_counts", lambda *x: {"traces": 0, "vias": 0})
    monkeypatch.setattr(a, "_board_counts", lambda *x: {"components": 2, "nets": 2})
    monkeypatch.setattr(
        a,
        "_annotation_summary",
        lambda *x: {
            "completed": True,
            "components": 2,
            "pins": 4,
            "nets": 2,
            "errors": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(a, "_PromptAnswerer", lambda *x: nullcontext(SimpleNamespace(answered=[])))
    if restore_fails:
        with pytest.raises(a.AdapterError) as failure:
            a._forward_annotate({"project": str(project), "verify": True}, object())
        result = failure.value.details["native_result"]
        assert failure.value.details["designer_restoration"]["code"] == "E_NOT_FOUND"
    else:
        result = a._forward_annotate({"project": str(project), "verify": True}, object())
    assert events == ["annotate", "save_layout", "reopen_layout", "restore_designer"]
    assert result["saved"] and result["persistence_verified"]
