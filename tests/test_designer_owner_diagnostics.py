"""Fixed private native boundaries and 151 automation protocol, all fake CAD."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_designer_native_owner import _application

from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Diagnostic unit test attempted a native loader or CAD factory")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    monkeypatch.setattr(O, "create_native_runtime", forbidden)
    monkeypatch.setattr(O.L, "_kernel", forbidden)
    monkeypatch.setattr(O.L, "_plain_path", lambda path: Path(path))
    monkeypatch.setattr(A, "_import_com", forbidden)
    yield
    assert hits == []


@pytest.fixture
def automation(monkeypatch, tmp_path):
    owner, raw, _, _ = _application(monkeypatch)
    owner.com.VT_I4 = 3
    trace = O._BoundaryTrace(tmp_path)
    owner.boundary = trace
    scene = NS(owner=owner, raw=raw, trace=trace, mode=0, calls=[], failure=None)
    monkeypatch.setattr(raw, "GetIDsOfNames", lambda lcid, name: 250)

    def invoke(*args):
        scene.calls.append(args)
        if args[2] == 2:
            assert args == (250, 0, 2, (3, 0), ())
            if scene.failure == "before" and len(scene.calls) == 1:
                raise RuntimeError("private endpoint and token")
            return 0 if scene.failure == "after_mismatch" else scene.mode
        assert args == (250, 0, 4, (0, 0), ((3, 1),), 1)
        if scene.failure == "setter":
            raise RuntimeError("private endpoint and token")
        scene.mode = 1

    monkeypatch.setattr(raw, "InvokeTypes", invoke)
    monkeypatch.setattr(D, "_check", lambda grant: owner)
    scope = NS(require=lambda **kw: kw == {"effects": ["session"]} or pytest.fail("wrong effects"))
    monkeypatch.setattr(D, "validate_editing_scope", lambda value: scope)
    yield scene
    if not trace.close_attempted:
        trace.close()


def test_silent_mode_initial_unknown_then_exact_get_put_get_and_readback(automation):
    scene = automation
    initial = scene.owner.automation_snapshot()
    assert initial["silent_mode_before"] is initial["silent_mode_after"] is None
    assert initial["before_observation"] == initial["after_observation"] == "unobserved"
    assert initial["set_attempted"] is False and initial["set_returned"] is None
    result = D.configure_automation(object(), {})
    assert result == {
        "silent_mode_before": 0,
        "before_observation": "observed",
        "set_attempted": True,
        "set_returned": True,
        "silent_mode_after": 1,
        "after_observation": "observed",
        "verified": True,
    }
    assert [call[2] for call in scene.calls] == [2, 4, 2]
    with pytest.raises(O.OwnerError, match="already_started_no_replay"):
        D.configure_automation(object(), {})
    assert len(scene.calls) == 3
    rows = [json.loads(line) for line in (Path(scene.trace.stream.name)).read_text().splitlines()]
    assert any(
        row["step"] == "automation.SilentMode.set_one" and row["event"] == "begin" for row in rows
    )


@pytest.mark.parametrize("failure", ["before", "setter", "after_mismatch"])
def test_silent_configuration_failure_keeps_distinct_observed_facts_and_no_replay(
    automation, failure
):
    scene = automation
    scene.failure = failure
    with pytest.raises((RuntimeError, O.OwnerError)):
        D.configure_automation(object(), {})
    value = scene.owner.automation_snapshot()
    if failure == "before":
        assert value["silent_mode_before"] is None and value["before_observation"] == "unobserved"
        assert value["set_attempted"] is False and value["set_returned"] is None
    elif failure == "setter":
        assert value["silent_mode_before"] == 0 and value["set_attempted"] is True
        assert value["set_returned"] is None and value["silent_mode_after"] is None
    else:
        assert value["set_returned"] is True and value["silent_mode_after"] == 0
        assert value["verified"] is False
    before = list(scene.calls)
    with pytest.raises(O.OwnerError, match="already_started_no_replay"):
        D.configure_automation(object(), {})
    assert scene.calls == before
    assert "private endpoint" not in Path(scene.trace.stream.name).read_text()


@pytest.mark.parametrize(
    "event,put_attempted,put_returned", [("begin", False, None), ("end", True, True)]
)
def test_trace_failure_around_setter_never_claims_configuration_success(
    automation, monkeypatch, event, put_attempted, put_returned
):
    scene = automation
    original = scene.trace.emit

    def fail(step, actual_event, **fields):
        if step == "automation.SilentMode.set_one" and actual_event == event:
            scene.trace.failed = True
            raise O.OwnerError("native_boundary_trace_incomplete")
        return original(step, actual_event, **fields)

    monkeypatch.setattr(scene.trace, "emit", fail)
    with pytest.raises(O.OwnerError) as error:
        D.configure_automation(object(), {})
    result = scene.owner.automation_snapshot()
    assert result["set_attempted"] is put_attempted and result["set_returned"] is put_returned
    assert result["verified"] is None and result["silent_mode_after"] is None
    assert getattr(error.value, "native_call_returned", None) is (True if event == "end" else None)


def test_required_begin_failure_stops_call_but_uninitialize_still_runs(tmp_path, monkeypatch):
    trace = O._BoundaryTrace(tmp_path)
    monkeypatch.setattr(O, "MAX_NATIVE_BOUNDARY_BYTES", 1)
    calls = []
    try:
        with A._owner_boundary_trace(trace):
            with pytest.raises(O.OwnerError, match="byte_limit"):
                A._native_call("create.NewProject", lambda: calls.append("NewProject"))
            A._native_call("worker.CoUninitialize", lambda: calls.append("CoUninitialize"))
        assert calls == ["CoUninitialize"]
        assert trace.snapshot()["incomplete"] is True
        with pytest.raises(O.OwnerError, match="trace_incomplete"):
            trace.require_complete()
    finally:
        trace.close()


def test_owner_trace_uses_only_fixed_private_filename_and_safe_summaries(tmp_path, monkeypatch):
    unowned = tmp_path / "caller-selected.ndjson"
    monkeypatch.setenv("XPEDITION_NATIVE_TRACE_FILE", str(unowned))
    trace = O._BoundaryTrace(tmp_path)
    try:
        with A._owner_boundary_trace(trace):
            assert A._native_call("create.NewProject", lambda: True) is True
        assert not unowned.exists()
        path = tmp_path / "native-boundary.ndjson"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["event"] for row in rows] == ["begin", "end"]
        assert rows[-1]["result"] is True
        with pytest.raises(O.OwnerError, match="field_contract_invalid"):
            trace.emit("create.NewProject", "observed", args="secret token")
        assert "secret" not in path.read_text() and trace.failed is True
    finally:
        trace.close()


def _generated_app(member=251):
    values = {"LCID": 0, "__name__": "fixture_current_151"}
    exec(
        "class App:\n"
        " def NewProject(self, ProjectPath, CentralLibraryPath, ServerName, ProjectTemplatePath):\n"
        "  return self._oleobj_.InvokeTypes("
        + str(member)
        + ",LCID,1,(11,0),((8,1),(8,1),(8,1),(8,1)),"
        "ProjectPath,CentralLibraryPath,ServerName,ProjectTemplatePath)",
        values,
    )
    return values["App"]()


@pytest.mark.parametrize("member,observed", [(251, True), (252, False)])
def test_actual_wrapper_function_declaration_is_inspected_not_a_cache_filename(member, observed):
    app = _generated_app(member)
    value = D._wrapper_observation(app)
    assert value == {
        "module": "fixture_current_151",
        "class": "App",
        "new_project_signature_observed": observed,
        "new_project_declaration_observation": "observed_matching"
        if observed
        else "observed_incompatible",
    }
    assert D._wrapper_observation(NS())["new_project_signature_observed"] is False


def test_scope_refusal_precedes_silent_mode_getters(automation, monkeypatch):
    def denied(**kw):
        raise D.DispatchError("scope_denied_fixture")

    monkeypatch.setattr(D, "validate_editing_scope", lambda scope: NS(require=denied))
    with pytest.raises(D.DispatchError, match="scope_denied"):
        D.configure_automation(object(), {})
    assert automation.calls == []
    assert automation.owner.automation_snapshot()["set_attempted"] is False


@pytest.mark.parametrize(
    "point,key,expected",
    [
        ("automation.SilentMode.before", "silent_mode_before", 0),
        ("automation.SilentMode.after", "silent_mode_after", 1),
    ],
)
def test_successful_get_fact_survives_end_trace_failure(
    automation, monkeypatch, point, key, expected
):
    scene = automation
    original = scene.trace.emit

    def fail(step, event, **fields):
        if step == point and event == "end":
            scene.trace.failed = True
            raise O.OwnerError("native_boundary_trace_incomplete")
        return original(step, event, **fields)

    monkeypatch.setattr(scene.trace, "emit", fail)
    with pytest.raises(O.OwnerError) as failure:
        D.configure_automation(object(), {})
    value = scene.owner.automation_snapshot()
    assert value[key] == expected
    assert (
        value["before_observation" if key.endswith("before") else "after_observation"]
        == "observed_boundary_incomplete"
    )
    assert value["verified"] is None
    facts = O._failure_facts(failure.value, "owner.automation_configuration", True)
    assert facts["native_call_returned"] is True and facts["native_result_summary"] == expected


@pytest.mark.parametrize(
    "declared,native_id,expected",
    [(251, 251, None), (252, 251, "signature_required"), (251, 252, "mapping_differs")],
)
def test_new_project_declaration_and_native_member_both_precede_write(
    tmp_path, monkeypatch, declared, native_id, expected
):
    trace = O._BoundaryTrace(tmp_path)
    app = _generated_app(declared)
    calls = []
    raw = NS(GetIDsOfNames=lambda lcid, name: calls.append((lcid, name)) or native_id)
    owner = NS(boundary=trace, raw=raw, probe=lambda: {"canonical_stable": True})
    grant = D._Grant(D._SEAL, owner, None, "a" * 32, app)
    monkeypatch.setattr(D, "_check", lambda selected: owner)
    token = D._CURRENT.set((grant, "clone_project"))
    try:
        with A._owner_boundary_trace(trace):
            if expected:
                with pytest.raises(D.DispatchError, match=expected):
                    D.check_new_project_wrapper(app)
            else:
                D.check_new_project_wrapper(app)
        assert calls == ([] if declared != 251 else [(0, "NewProject")])
        rows = [json.loads(line) for line in Path(trace.stream.name).read_text().splitlines()]
        assert rows[0]["wrapper"]["new_project_signature_observed"] is (declared == 251)
        assert any(row["step"] == "create.NewProject.declaration" for row in rows) is (
            expected is None
        )
    finally:
        D._CURRENT.reset(token)
        trace.close()


def test_dynamic_without_python_method_code_uses_current_native_contract(tmp_path, monkeypatch):
    trace = O._BoundaryTrace(tmp_path)
    app, reads = NS(), []
    raw = NS(GetIDsOfNames=lambda lcid, name: reads.append(name) or 251)
    owner = NS(boundary=trace, raw=raw, probe=lambda: reads.append("current151probe") or {})
    grant = D._Grant(D._SEAL, owner, None, "a" * 32, app)
    monkeypatch.setattr(D, "_check", lambda selected: owner)
    token = D._CURRENT.set((grant, "clone_project"))
    try:
        with A._owner_boundary_trace(trace):
            D.check_new_project_wrapper(app)
        assert reads == ["current151probe", "NewProject"]
        rows = [json.loads(line) for line in Path(trace.stream.name).read_text().splitlines()]
        assert rows[0]["wrapper"]["new_project_declaration_observation"] == "unobserved"
        assert rows[-1]["declaration_basis"] == "current_role_151_and_native_member_251"
    finally:
        D._CURRENT.reset(token)
        trace.close()
