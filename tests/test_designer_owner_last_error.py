"""Same-held post-False diagnostics with private text; no CAD or native imports."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from test_designer_owner_diagnostics import no_native  # noqa: F401
from test_native_project_creation import ProjectApp, _stock

from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


@pytest.fixture
def case(tmp_path, monkeypatch):
    source = _stock(tmp_path)
    target = tmp_path / "fresh-target/Board.prj"
    runtime = tmp_path / "private-owner"
    runtime.mkdir()
    trace = O._BoundaryTrace(runtime)
    trace.select_command("owner-command-" + "a" * 32)
    initial = runtime / "initial.json"
    initial.write_bytes(b"immutable original unknown")
    app = ProjectApp()
    state = NS(events=[], text="untrusted scope token:private", failing=None, bad_member=False)

    class GetterError(Exception):
        hresult = -2147417848

    def member(lcid, name):
        state.events.append(("member", name))
        return 999 if state.bad_member else {"GetLastErrorId": 240, "GetLastErrorString": 239}[name]

    def invoke(dispid, lcid, flags, result_type, args):
        state.events.append(("getter", dispid))
        assert flags == 1 and args == () and result_type == (3 if dispid == 240 else 8, 0)
        if state.failing == dispid:
            raise GetterError("private endpoint and secret token")
        return 0 if dispid == 240 else state.text

    raw = NS(GetIDsOfNames=member, InvokeTypes=invoke)
    owner = NS(raw=raw, com=NS(VT_I4=3, VT_BSTR=8), boundary=trace)
    grant = D._Grant(D._SEAL, owner, None, "b" * 32, app)
    monkeypatch.setattr(D, "_check", lambda selected: owner)
    monkeypatch.setattr(D, "check_new_project_wrapper", lambda selected: None)
    # This fixture isolates post-False error reads. The complete local TypeLib
    # preflight and its refusal paths have separate fixed-writer coverage.
    monkeypatch.setattr(
        D,
        "preflight_document_members",
        lambda selected: {
            "scheme": "designer-fixed-document-call-v1",
            "operation": "preflight",
            "status": "metadata_verified",
            "contracts": {
                name: {"verified": True} for name in ("open_design", "save_all", "save_document")
            },
            "invoke_attempted": False,
            "cleanup": {"status": "observed", "resources_released": True},
        },
    )
    monkeypatch.setattr(A, "_owner_application", lambda: app)
    monkeypatch.setattr(A, "_viewdraw_application", lambda *a, **kw: app)

    def rejected(*args):
        state.events.append(("NewProject",))
        return False

    app.NewProject = rejected
    token = D._CURRENT.set((grant, "clone_project"))
    state.source, state.target, state.trace, state.runtime, state.initial = (
        source,
        target,
        trace,
        runtime,
        initial,
    )
    state.run = lambda: A._clone_project(
        {"template": str(source), "project": str(target)}, object()
    )
    yield state
    D._CURRENT.reset(token)
    trace.close()


@pytest.mark.parametrize("text", ["untrusted scope token:private", ""])
def test_false_queries_preverified_same_raw_once_and_keeps_text_private(case, text):
    case.text = text
    before = case.initial.read_bytes()
    with pytest.raises(A.AdapterError) as rejected:
        case.run()
    error = rejected.value
    assert error.code == "E_PROJECT_INVALID" and error.details["acknowledgement"] is False
    assert case.events == [
        ("member", "GetLastErrorId"),
        ("member", "GetLastErrorString"),
        ("NewProject",),
        ("getter", 240),
        ("getter", 239),
    ]
    summary = error.details["new_project_last_error"]
    assert summary["last_error_id"] == 0 and summary["causal_attribution"] == "unproven"
    assert summary["text_utf8_bytes"] == len(text.encode())
    assert summary["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert summary["new_project_acknowledgement"] is False
    private = json.loads((case.runtime / "native-new-project-last-error.json").read_text())
    assert private["text"] == text and private["_untrusted"] == ["text"]
    public = O._adapter_failure(error)
    assert public["details"]["new_project_last_error"] == summary
    assert "token:private" not in json.dumps(public)
    assert "token:private" not in (case.runtime / "native-boundary.ndjson").read_text()
    assert case.initial.read_bytes() == before and not case.target.exists()


@pytest.mark.parametrize("failing", [240, 239])
def test_each_getter_exception_is_null_without_masking_false_or_retry(case, failing):
    case.failing = failing
    with pytest.raises(A.AdapterError) as rejected:
        case.run()
    assert rejected.value.code == "E_PROJECT_INVALID"
    assert rejected.value.details["acknowledgement"] is False
    result = rejected.value.details["new_project_last_error"]
    key = "id" if failing == 240 else "text"
    assert result[key + "_observation"] == "exception"
    assert result[key + "_hresult"] == -2147417848
    assert result["last_error_id" if failing == 240 else "text_sha256"] is None
    assert case.events.count(("getter", 240)) == case.events.count(("getter", 239)) == 1
    assert case.events.count(("NewProject",)) == 1
    assert "secret token" not in json.dumps(O._adapter_failure(rejected.value))


@pytest.mark.parametrize("fault", ["oversize", "member"])
def test_oversize_text_is_not_retained_and_wrong_member_refuses_before_newproject(case, fault):
    if fault == "member":
        case.bad_member = True
        with pytest.raises(D.DispatchError, match="member_mapping_differs"):
            case.run()
        assert not any(event[0] == "NewProject" for event in case.events)
    else:
        case.text = "x" * 8193
        with pytest.raises(A.AdapterError) as rejected:
            case.run()
        result = rejected.value.details["new_project_last_error"]
        assert result["text_observation"] == "over_limit"
        assert result["private_text_retained"] is False and result["text_sha256"] is None
        assert rejected.value.details["acknowledgement"] is False
        assert case.events.count(("NewProject",)) == 1
    assert not (case.runtime / "native-new-project-last-error.json").exists()
