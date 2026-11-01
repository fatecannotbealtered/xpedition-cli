"""Owner first-open and backup routing boundaries; every CAD object is a fake."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_designer_owner_dispatch import FACTORY, editing_scope, scene  # noqa: F401

from xpedition_cli import designer_first_open as F
from xpedition_cli import designer_host_bootstrap as B
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli import native_com_adapter as A


@pytest.fixture
def opened_bridge(scene, editing_scope, monkeypatch, tmp_path):  # noqa: F811 - imported pytest fixtures
    value = scene
    value.target = str(tmp_path / "copy.prj")
    (tmp_path / "copy.prj").write_bytes(b"fixture closed copy, not a CAD database")
    value.scope = editing_scope
    value.members, value.invokes, value.verifications = [], [], []
    value.ack, value.blocking_document = True, None
    for name, constant in (
        ("VT_BOOL", 11),
        ("VT_BSTR", 8),
        ("VT_DISPATCH", 9),
        ("VT_EMPTY", 0),
        ("VT_I4", 3),
    ):
        setattr(value.com, name, constant)
    raw = value.owner.raw
    original_member = raw.GetIDsOfNames
    original_invoke = raw.InvokeTypes

    def member(lcid, name):
        value.members.append(name)
        declared = {
            "ActiveDocument": 208,
            "OpenProject": 227,
            "Quit": 226,
            "GetLastErrorId": 240,
            "GetLastErrorString": 239,
        }
        return declared[name] if name in declared else original_member(lcid, name)

    def invoke(dispid, lcid, kind, result_type, arguments, *values):
        value.invokes.append((dispid, kind, values))
        if dispid == 208:
            return value.blocking_document
        if dispid == 226:
            return None
        if dispid == 240:
            return 0
        if dispid == 239:
            return ""
        if dispid != 227:
            return original_invoke(dispid, lcid, kind, result_type, arguments, *values)
        assert (dispid, kind, result_type, arguments) == (227, 1, (11, 0), ((8, 1),))
        if value.ack is True:
            value.document = {"open": True, "path": B.W._path(values[0])}
        return value.ack

    raw.GetIDsOfNames, raw.InvokeTypes = member, invoke

    class App:
        _oleobj_ = raw
        Name, FullName, Version = "owned fixture", value.owner.expected.executable, "2604-fixture"

        @property
        def ActiveView(self):
            return object() if value.document["open"] else None

        @property
        def ActiveDocument(self):
            return NS(Save=lambda: None) if value.document["open"] else None

        def IsProjectOpened(self):
            return value.document["open"]

        def GetProjectData(self):
            return NS(
                GetProjectFilePath=lambda: value.document["path"] or "",
                GetiCDBDesigns=lambda: NS(GetCount=lambda: 1, GetItem=lambda index: "Board1"),
            )

    value.app = App()
    value.grant.app = value.app
    value.owner.trusted_config_root = lambda: tmp_path / "config"
    value.capsule = {"protocol": F.PROTOCOL, "project": value.target}
    value.proof = F.VerifiedClosedCopyOpen(F._SEAL, value.capsule)

    def verify(capsule, **kwargs):
        value.verifications.append((copy.deepcopy(capsule), kwargs))
        assert kwargs["project"] == value.target and kwargs["sdk_operation_id"] == "op-open"
        assert kwargs["own_generation_reader"].__self__ is value.owner
        return value.proof

    monkeypatch.setattr(F, "verify_closed_copy_open_binding", verify)
    monkeypatch.setattr(A, "_viewdraw_application", FACTORY)
    monkeypatch.setattr(A, "_import_com", lambda: (value.com, NS()))
    monkeypatch.setattr(
        A, "_PromptAnswerer", lambda *a, **k: pytest.fail("Owner must not answer GUI")
    )
    monkeypatch.setattr(A, "_settle", lambda *a: None)
    value.binding = {
        "command": "session attach",
        "scope_id": editing_scope["scope_id"],
        "scope_sha256": editing_scope["scope_sha256"],
        "source_operation_id": "op-open",
        "closed_copy_open": value.capsule,
    }
    value.open = lambda: D.dispatch(
        value.grant,
        "open",
        {"project": value.target, "domain": "schematic"},
        editing_scope=value.scope,
        sdk_operation_id="op-open",
        authorization_binding=value.binding,
    )
    return value


def test_first_open_independently_verifies_then_sticky_binds_same_raw_and_reads_back(opened_bridge):
    value = opened_bridge
    result = value.open()
    assert result["opened"] is True and result["open_project_verified"] is True
    assert value.owner.project == value.target and value.owner.project_origin is value.proof
    assert value.owner.project_open_verified is True
    assert len(value.verifications) == 1
    assert [entry[0] for entry in value.invokes].count(227) == 1
    before = list(value.invokes)
    with pytest.raises(D.DispatchError, match="not_reusable"):
        value.open()
    assert value.invokes == before


def _observation_report(status):
    return {
        "profile": "first-open-fixture-v1",
        "budget": {"inputs": {"files": 2, "project_bytes": 64}, "total_seconds": 30.0},
        "elapsed_seconds": 0.25,
        "phase_elapsed_seconds": {"target_tree_repeat": 0.25},
        "phase": "target_tree_repeat",
        "status": status,
        "failed_phase": None if status == "completed" else "target_tree_repeat",
    }


def test_first_open_observation_report_comes_from_actual_helper_outside_binding(
    opened_bridge, monkeypatch
):
    value = opened_bridge
    report = _observation_report("completed")
    monkeypatch.setattr(F, "last_observation_report", lambda: report)
    value.binding["closed_copy_open"]["first_open_observation_report"] = {"forged": True}
    proof_before = copy.deepcopy(value.proof.safe_binding)
    result = value.open()
    assert result["first_open_observation_report"] == report
    result["first_open_observation_report"]["phase_elapsed_seconds"]["target_tree_repeat"] = 99
    assert report["phase_elapsed_seconds"]["target_tree_repeat"] == 0.25
    assert value.proof.safe_binding == proof_before
    assert [entry[0] for entry in value.invokes].count(227) == 1


def test_first_open_refusal_preserves_same_helper_report_reason_and_no_native_dispatch(
    opened_bridge, monkeypatch
):
    from xpedition_cli import designer_native_owner as O
    from xpedition_cli.errors import CLIError

    value = opened_bridge
    report = _observation_report("refused")
    original = CLIError(
        "E_CONFLICT",
        "fixture verifier refused",
        {"reason": "target_changed_during_first_open_observation", "write_attempted": False},
    )

    def rejected(*a, **k):
        raise original

    monkeypatch.setattr(F, "verify_closed_copy_open_binding", rejected)
    monkeypatch.setattr(F, "last_observation_report", lambda: report)
    with pytest.raises(CLIError) as failed:
        value.open()
    assert failed.value is original and failed.value.dispatch_started is False
    failure = O._adapter_failure(failed.value)
    assert failure["details"]["first_open_observation_report"] == report
    assert failure["details"]["reason"] == "target_changed_during_first_open_observation"
    assert failure["details"]["write_attempted"] is False
    assert failure["details"]["native_request_dispatched"] is False
    assert failure["details"]["native_call_unknown"] is False
    assert not any(entry[0] == 227 for entry in value.invokes)
    failure["details"]["first_open_observation_report"]["elapsed_seconds"] = 99
    assert report["elapsed_seconds"] == 0.25


def test_report_getter_failure_does_not_mask_verifier_refusal_or_accept_json_report(
    opened_bridge, monkeypatch
):
    from xpedition_cli import designer_native_owner as O
    from xpedition_cli.errors import CLIError

    value = opened_bridge
    original = CLIError(
        "E_CONFLICT",
        "fixture original refusal",
        {
            "reason": "exact_first_open_capsule_required",
            "first_open_observation_report": {"forged": True},
        },
    )

    def rejected(*a, **k):
        raise original

    def broken_report():
        raise ValueError("private report failure")

    monkeypatch.setattr(F, "verify_closed_copy_open_binding", rejected)
    monkeypatch.setattr(F, "last_observation_report", broken_report)
    with pytest.raises(CLIError) as failed:
        value.open()
    failure = O._adapter_failure(failed.value)
    assert failed.value is original
    assert failure["details"]["reason"] == "exact_first_open_capsule_required"
    assert "first_open_observation_report" not in failure["details"]
    assert not any(entry[0] == 227 for entry in value.invokes)


@pytest.mark.parametrize("fault", ["json_verified", "wrong_target", "nonempty_document"])
def test_first_open_rejects_unproved_or_nonempty_targets_before_open(opened_bridge, fault):
    value = opened_bridge
    if fault == "json_verified":
        value.proof = {"verified": True, "project": value.target}
    elif fault == "wrong_target":
        value.proof = F.VerifiedClosedCopyOpen(F._SEAL, {"project": value.target + ".other"})
    else:
        value.blocking_document = object()
    with pytest.raises((D.DispatchError, ValueError)):
        value.open()
    assert value.owner.project is None
    assert not any(entry[0] == 227 for entry in value.invokes)


def test_false_open_never_becomes_editable_or_rebinds_target(opened_bridge):
    value = opened_bridge
    value.ack = False
    with pytest.raises((D.DispatchError, A.AdapterError)):
        value.open()
    assert value.owner.project == value.target and value.owner.project_origin is value.proof
    assert value.owner.project_open_verified is False
    assert [entry[0] for entry in value.invokes].count(227) == 1
    with pytest.raises(D.DispatchError, match="not_reusable"):
        value.open()
    assert [entry[0] for entry in value.invokes].count(227) == 1


def test_owner_lifecycle_preflight_never_uses_legacy_application_inventory(
    opened_bridge, monkeypatch
):
    value = opened_bridge
    value.open()
    writer_scene = {"allowed_live_generations": [], "schematic": [], "layout": [], "library": []}
    monkeypatch.setattr(F, "observe_owned_designer_scene", lambda **kw: copy.deepcopy(writer_scene))
    monkeypatch.setattr(
        A, "_prepare_project_release", lambda *a: pytest.fail("Foreign COM inventory reached")
    )
    monkeypatch.setattr(
        A,
        "_import_com",
        lambda: pytest.fail("Already initialized owner must not import a fallback"),
    )
    binding = {
        "command": "project backup",
        "scope_id": value.scope["scope_id"],
        "scope_sha256": value.scope["scope_sha256"],
        "source_operation_id": "op-backup",
    }
    result = D.dispatch(
        value.grant,
        "release_project_preflight",
        {"project": value.target, "observe_only": True},
        editing_scope=value.scope,
        sdk_operation_id="op-backup",
        authorization_binding=binding,
    )
    assert result["read_only"] is True and result["save_count"] == result["close_count"] == 0
    assert result["release_preflight"]["library"]["processes"] == []


def _close_model(value, monkeypatch, *, close_reply=True, save_failure=False):
    from xpedition_cli import designer_native_owner as O

    original = value.owner.raw.InvokeTypes
    original_member = value.owner.raw.GetIDsOfNames
    counts = {"save": 0, "close": 0}
    docs = NS(QueryInterface=lambda *args: object(), GetIDsOfNames=lambda lcid, name: 10)

    def saved(*args):
        assert args == (10, 0, 1, (0, 0), ())
        counts["save"] += 1
        if save_failure:
            raise RuntimeError("synthetic save reply unavailable")

    docs.InvokeTypes = saved

    def member(lcid, name):
        return {"Documents": 1, "CloseProject": 274}.get(name) or original_member(lcid, name)

    def invoke(dispid, *args):
        if dispid == 1:
            return docs
        if dispid == 274:
            counts["close"] += 1
            value.document = {"open": False, "path": None}
            return close_reply
        return original(dispid, *args)

    monkeypatch.setattr(value.owner.raw, "GetIDsOfNames", member)
    monkeypatch.setattr(value.owner.raw, "InvokeTypes", invoke)

    def fixed_save(operation, argument=None, *, on_dispatch=None, on_return=None):
        # Isolate sequencing at the supported fixed-document bridge. Its real
        # owner/typelib/ABI checks have independent positive and negative tests.
        assert operation == "save_all" and argument is None
        assert docs.QueryInterface(O.DOCS_IID) is not None
        on_dispatch()
        docs.InvokeTypes(10, 0, 1, (0, 0), ())
        on_return()

    monkeypatch.setattr(value.owner, "_invoke_fixed_document", fixed_save)
    monkeypatch.setattr(B, "_raw", lambda obj, com: obj)
    writer_scene = {"allowed_live_generations": [], "schematic": [], "layout": [], "library": []}
    monkeypatch.setattr(F, "observe_owned_designer_scene", lambda **kw: copy.deepcopy(writer_scene))
    monkeypatch.setattr(
        A, "_import_com", lambda: pytest.fail("Lifecycle must not acquire another COM app")
    )
    return counts


def _backup_call(value, operation, method, params):
    binding = {
        "command": "project backup",
        "scope_id": value.scope["scope_id"],
        "scope_sha256": value.scope["scope_sha256"],
        "source_operation_id": operation,
    }
    return D.dispatch(
        value.grant,
        method,
        {"project": value.target, **params},
        editing_scope=value.scope,
        sdk_operation_id=operation,
        authorization_binding=binding,
    )


def test_successful_close_open_allows_another_distinct_cycle_without_rewriting_prior_facts(
    opened_bridge, monkeypatch
):
    value = opened_bridge
    value.open()
    counts = _close_model(value, monkeypatch)
    first = None
    for number in (1, 2):
        operation = "op-backup-" + str(number)
        preflight = _backup_call(
            value, operation, "release_project_preflight", {"observe_only": True}
        )
        released = _backup_call(
            value, operation, "release_project", {"expected_release_preflight": preflight}
        )
        assert released["quiescent"] is True and released["designer_closed"] is True
        assert (
            value.owner._save_close_started is True and value.owner.project_open_verified is False
        )
        if number == 1:
            first = copy.deepcopy(released)
        reopened = _backup_call(value, operation, "reopen_project", {})
        assert reopened["reopened"] is True
        assert (
            value.owner._save_close_started is False and value.owner.project_open_verified is True
        )
        if number == 1:
            assert released == first
    assert counts == {"save": 2, "close": 2}
    assert [entry[0] for entry in value.invokes].count(227) == 3


def test_changeset_persistence_uses_same_owner_save_close_empty_and_exact_reopen(
    opened_bridge, monkeypatch
):
    value = opened_bridge
    value.open()
    counts = _close_model(value, monkeypatch)
    monkeypatch.setattr(
        A, "dispatch", lambda method, params: D.persist_changeset_project(value.app, value.target)
    )
    binding = {
        "command": "schematic edit",
        "scope_id": value.scope["scope_id"],
        "scope_sha256": value.scope["scope_sha256"],
        "source_operation_id": "op-edit",
    }
    result = D.dispatch(
        value.grant,
        "apply_changeset",
        {"project": value.target},
        editing_scope=value.scope,
        sdk_operation_id="op-edit",
        authorization_binding=binding,
    )
    assert result["source"] == "designer_save_close_reopen"
    assert [result[key] for key in ("save_count", "close_count", "reopen_count")] == [1, 1, 1]
    assert (
        result["save_acknowledged"] is result["closed_observed"] is result["close_returned"] is True
    )
    assert result["before_identity"] == result["reopened_identity"]
    assert result["before_pid"] == result["reopened_pid"] == value.owner.expected.pid
    assert counts == {"save": 1, "close": 1}
    assert value.owner.project_open_verified is True and value.owner._save_close_started is False


def test_changeset_close_failure_never_dispatches_reopen_or_resets_owner_lifecycle(
    opened_bridge, monkeypatch
):
    value = opened_bridge
    value.open()
    counts = _close_model(value, monkeypatch, close_reply=False)
    monkeypatch.setattr(
        A, "dispatch", lambda method, params: D.persist_changeset_project(value.app, value.target)
    )
    binding = {
        "command": "schematic edit",
        "scope_id": value.scope["scope_id"],
        "scope_sha256": value.scope["scope_sha256"],
        "source_operation_id": "op-edit",
    }
    with pytest.raises(ValueError) as failed:
        D.dispatch(
            value.grant,
            "apply_changeset",
            {"project": value.target},
            editing_scope=value.scope,
            sdk_operation_id="op-edit",
            authorization_binding=binding,
        )
    assert failed.value.lifecycle["save_acknowledged"] is True
    assert failed.value.lifecycle["close_returned"] is False
    assert failed.value.lifecycle["reopen_count"] == 0
    assert counts == {"save": 1, "close": 1}
    assert [entry[0] for entry in value.invokes].count(227) == 1
    assert value.owner._save_close_started is True and value.owner.project_open_verified is False


def test_failed_close_cannot_reopen_or_reset_lifecycle(opened_bridge, monkeypatch):
    value = opened_bridge
    value.open()
    counts = _close_model(value, monkeypatch, close_reply=False)
    preflight = _backup_call(
        value, "op-failed-close", "release_project_preflight", {"observe_only": True}
    )
    with pytest.raises(ValueError):
        _backup_call(
            value, "op-failed-close", "release_project", {"expected_release_preflight": preflight}
        )
    prior_opens = [entry[0] for entry in value.invokes].count(227)
    with pytest.raises(ValueError, match="incomplete_owner_close"):
        _backup_call(value, "op-no-replay", "reopen_project", {})
    assert value.owner._save_close_started is True and value.owner.project_open_verified is False
    assert [entry[0] for entry in value.invokes].count(227) == prior_opens
    assert counts == {"save": 1, "close": 1}


def test_missing_save_reply_never_closes_reopens_or_resets_lifecycle(opened_bridge, monkeypatch):
    value = opened_bridge
    value.open()
    counts = _close_model(value, monkeypatch, save_failure=True)
    preflight = _backup_call(
        value, "op-failed-save", "release_project_preflight", {"observe_only": True}
    )
    with pytest.raises(RuntimeError, match="synthetic save reply unavailable"):
        _backup_call(
            value, "op-failed-save", "release_project", {"expected_release_preflight": preflight}
        )
    before = list(value.invokes)
    with pytest.raises(ValueError, match="incomplete_owner_close"):
        _backup_call(value, "op-no-save-replay", "reopen_project", {})
    assert value.invokes == before
    assert counts == {"save": 1, "close": 0}
    assert value.owner._save_close_started is True
    assert value.owner.project_open_verified is False
    assert value.document["open"] is True
    facts = value.owner.lifecycle_snapshot()
    assert facts["save_call_attempted"] is True and facts["save_call_returned"] is None
    assert facts["close_call_attempted"] is False and facts["close_call_returned"] is None


@pytest.mark.parametrize("fault", ["empty", "getter_error", "partial"])
def test_false_open_retains_exact_diagnostics_and_quarantines_without_masking_reply(
    opened_bridge, monkeypatch, tmp_path, fault
):
    import json

    from xpedition_cli import designer_native_owner as O

    value = opened_bridge
    monkeypatch.setattr(O.L, "_plain_path", lambda path: Path(path))
    trace_root = tmp_path / "trace"
    trace_root.mkdir()
    trace = O._BoundaryTrace(trace_root)
    trace.select_command("owner-command-" + "c" * 32)
    value.owner.boundary = trace
    value.ack = False
    original = value.owner.raw.InvokeTypes

    class GetterError(Exception):
        hresult = -2147417848

    def invoke(dispid, *args):
        if dispid == 239 and fault == "getter_error":
            value.invokes.append((239, 1, ()))
            raise GetterError("private error text must not leak")
        result = original(dispid, *args)
        if dispid == 227 and fault == "partial":
            value.document = {"open": True, "path": B.W._path(value.target)}
        return result

    monkeypatch.setattr(value.owner.raw, "InvokeTypes", invoke)
    try:
        with pytest.raises(A.AdapterError) as rejected:
            value.open()
        assert rejected.value.code == "E_PROJECT_INVALID"
        assert rejected.value.details["open_acknowledgement"] is False
        summary = rejected.value.details["open_project_last_error"]
        assert summary["open_project_acknowledgement"] is False
        assert summary["last_error_id"] == 0 and summary["causal_attribution"] == "unproven"
        assert summary["text_observation"] == (
            "exception" if fault == "getter_error" else "observed"
        )
        if fault == "getter_error":
            assert summary["text_hresult"] == -2147417848 and summary["text_sha256"] is None
        else:
            assert summary["text_utf8_bytes"] == 0
            assert (
                json.loads((trace_root / "native-open-project-last-error.json").read_text())["text"]
                == ""
            )
        order = [entry[0] for entry in value.invokes]
        pos = order.index(227)
        assert order[pos : pos + 3] == [227, 240, 239]
        assert order.count(227) == 1
        q = D.quarantine_rejected_creation(
            value.grant,
            rejected.value,
            sdk_operation_id="op-open",
            command_id="owner-command-" + "c" * 32,
        )
        assert (
            q["reason"] == "open_project_returned_false"
            and q["engineering_writes_enabled"] is False
        )
        assert O._adapter_failure(rejected.value)["details"]["open_project_last_error"] == summary
        with pytest.raises(D.DispatchError, match="readonly_quarantine_blocks"):
            D.dispatch(value.grant, "save", {})
        if fault == "partial":
            with pytest.raises(O.OwnerError, match="fresh_empty_project"):
                value.owner.quit_empty()
            assert value.owner.quit_attempted is False
        else:
            assert value.owner.quit_empty()["quit_reply_received"] is True
    finally:
        trace.close()
