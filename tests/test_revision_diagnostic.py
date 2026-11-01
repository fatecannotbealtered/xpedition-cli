"""Request-local conflict diagnostics never query CAD or expose binding secrets."""

import copy
import json
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_native_snapshot_revision import attribute_native

from xpedition_cli import driver as D
from xpedition_cli.operation_budget import profile_for


def case():
    target = {"extensions": {"command": "diagnostic fixture", "source_operation_id": "op-current"}}
    facts = {"native": attribute_native(), "inputs": []}
    before = {"revision": "a" * 64, "extensions": {"facts": facts}}
    current = copy.deepcopy(before)
    current["revision"] = "b" * 64
    current["extensions"]["facts"]["native"]["components"][0]["attribute_display"][0]["value"] = (
        "22k"
    )
    record = {
        "operation_id": "op-current",
        "status": "planned",
        "receipt": None,
        "plan": {
            "operation_id": "op-current",
            "plan_id": "plan-current",
            "target": target,
            "base_snapshot": before,
        },
    }
    token = object()
    witness = {
        "request_token": token,
        "source_operation_id": "op-current",
        "target_digest": D._digest(target),
        "snapshot": current,
    }
    details = {
        "plan_id": "plan-current",
        "expected": {"revision": "a" * 64},
        "conflict": "state_drift",
    }
    return NS(target=target, record=record, token=token, witness=witness, details=details)


@pytest.mark.parametrize("bad", ["source", "plan", "target", "base", "request", "status"])
def test_wrong_association_never_discloses_unrelated_plan_material(bad):
    c = case()
    if bad == "source":
        c.witness["source_operation_id"] = "op-other"
    elif bad == "plan":
        c.details["plan_id"] = "plan-other"
    elif bad == "target":
        c.witness["target_digest"] = "c" * 64
    elif bad == "base":
        c.details["expected"]["revision"] = "c" * 64
    elif bad == "request":
        c.witness["request_token"] = object()
    else:
        c.record["status"] = "unknown"
    with pytest.raises(ValueError):
        D._matched_revision_diagnostic(c.witness, c.record, c.details, c.token)


def test_observed_real_value_position_and_font_changes_remain_visible_as_safe_paths():
    c = case()
    current = c.witness["snapshot"]["extensions"]["facts"]["native"]
    display = current["components"][0]["attribute_display"][0]["display"]
    display["x"] += 3
    display["font"] = "private-font-name"
    result = D._matched_revision_diagnostic(c.witness, c.record, c.details, c.token)
    paths = [r["path"] for r in result["changed_paths"]]
    assert any(p.endswith(".value") for p in paths)
    assert any(p.endswith(".x") for p in paths) and any(p.endswith(".font") for p in paths)
    assert "22k" not in json.dumps(result) and "private-font-name" not in json.dumps(result)


def test_secrets_are_omitted_without_hashing_their_values(monkeypatch):
    secret = "DO_NOT_HASH_OR_PRINT_THIS_BINDING"
    before = {
        k: {"nested": secret}
        for k in ("scope", "token", "key", "epoch", "journal", "runtime", "OBJREF", "license")
    }
    after = copy.deepcopy(before)
    original = D.hashlib.sha256

    def digest(value):
        assert secret.encode() not in value
        return original(value)

    monkeypatch.setattr(D.hashlib, "sha256", digest)
    result = D._revision_field_diagnostic(before, after)
    assert result["redacted_bindings"] and secret not in json.dumps(result)
    assert result["changed_paths"] == []


def test_nested_capability_and_binding_blocks_are_not_traversed_or_hashed(monkeypatch):
    secrets = ["CAPSULE_SECRET", "OPAQUE_BINDING_SECRET", "CAPABILITY_SECRET"]
    inner = "PRIVATE_INNER_BINDING_KEY"
    before = {
        "application": {
            "capability_capsule": {inner: secrets[0]},
            "dedicated_startup_binding": {inner: secrets[1]},
            "opaque_binding": {inner: secrets[2]},
        }
    }
    after = copy.deepcopy(before)
    after["application"]["opaque_binding"][inner] = "CHANGED_BINDING_SECRET"
    original = D.hashlib.sha256

    def digest(value):
        assert not any(
            word.encode() in value for word in [*secrets, inner, "CHANGED_BINDING_SECRET"]
        )
        return original(value)

    monkeypatch.setattr(D.hashlib, "sha256", digest)
    result = D._revision_field_diagnostic(before, after)
    rendered = json.dumps(result)
    assert result["redacted_bindings"] and result["changed_paths"] == []
    assert not any(word in rendered for word in [*secrets, inner, "CHANGED_BINDING_SECRET"])


def test_diagnostic_has_hard_node_and_path_bounds():
    large = D._revision_field_diagnostic({"large": [0] * 20000}, {"large": [1] * 20000})
    assert large["status"] == "truncated" and large["nodes_examined"] == 10000
    small = D._revision_field_diagnostic(
        {str(i): 0 for i in range(100)}, {str(i): 1 for i in range(100)}, max_paths=3
    )
    assert small["status"] == "truncated" and len(small["changed_paths"]) <= 3


def test_confirm_witness_is_the_already_captured_observation_without_second_query(monkeypatch):
    adapter = D.Adapter(backend=NS())
    target = {
        "extensions": {
            "command": "diagnostic fixture",
            "source_operation_id": "op-current",
            "operation_budget_profile": profile_for("diagnostic fixture"),
        }
    }
    snapshot = {"revision": "b" * 64, "extensions": {"facts": {"inputs": []}}}
    calls = []
    monkeypatch.setattr(adapter, "_observe", lambda selected: calls.append(selected) or snapshot)
    assert adapter.revision(target) == "b" * 64 and len(calls) == 1
    assert adapter._revision_witness["snapshot"] is snapshot


def test_finish_appends_only_diagnostic_and_preserves_original_plan_and_conflict(monkeypatch):
    c = case()
    saved = copy.deepcopy(c.record)
    gets = []
    cli = object.__new__(D.XpeditionDriverCLI)
    cli.adapter = NS(_revision_witness=c.witness, _revision_request_token=c.token)
    cli._store = NS(get=lambda source: gets.append(source) or c.record)
    monkeypatch.setattr(D.DriverCLI, "_finish", lambda self, envelope, *args: envelope)
    envelope = {
        "ok": False,
        "error": {"code": "E_CONFLICT", "message": "original conflict", "details": c.details},
    }
    result = cli._finish(envelope, 0, {}, None)
    assert (
        result["error"]["code"] == "E_CONFLICT"
        and result["error"]["message"] == "original conflict"
    )
    assert result["error"]["details"]["revision_diagnostic"]["current_revision"] == "b" * 64
    assert gets == ["op-current"] and c.record == saved and c.record["receipt"] is None
    assert cli.adapter._revision_witness is None


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_observation_keeps_original_conflict_strict_json_and_plan(monkeypatch, nonfinite):
    c = case()
    c.witness["snapshot"]["extensions"]["facts"]["metadata"] = {"x": nonfinite}
    with pytest.raises(ValueError, match="nonfinite_diagnostic_value"):
        D._revision_field_diagnostic({}, {"metadata": {"x": nonfinite}})
    saved = copy.deepcopy(c.record)
    cli = object.__new__(D.XpeditionDriverCLI)
    cli.adapter = NS(_revision_witness=c.witness, _revision_request_token=c.token)
    cli._store = NS(get=lambda source: c.record)
    monkeypatch.setattr(D.DriverCLI, "_finish", lambda self, envelope, *args: envelope)
    result = cli._finish(
        {
            "ok": False,
            "error": {"code": "E_CONFLICT", "message": "original conflict", "details": c.details},
        },
        0,
        {},
        None,
    )
    assert result["error"]["code"] == "E_CONFLICT"
    assert result["error"]["message"] == "original conflict"
    assert result["error"]["details"]["revision_diagnostic"]["status"] == "unavailable"
    assert c.record == saved and c.record["receipt"] is None
    assert cli.adapter._revision_witness is None
    json.dumps(result, allow_nan=False)


def test_complete_display_reordering_is_neutral_in_safe_diagnostic():
    before = {"native": attribute_native()}
    after = copy.deepcopy(before)
    after["native"]["components"][0]["attribute_display"].reverse()
    result = D._revision_field_diagnostic(before, after)
    assert result["status"] == "available" and result["changed_paths"] == []
