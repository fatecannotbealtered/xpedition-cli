"""Pure accounting/gating tests. No ACL, native query or CAD call is performed."""

from __future__ import annotations

import ctypes
import json

import pytest

from xpedition_cli import startup_input_seal as S


def test_product_creation_is_unqualified_before_any_io(monkeypatch):
    monkeypatch.setattr(S, "_native_granted_access", lambda handle: pytest.fail("no native call"))
    for parent in ("existing/private/root", None, object()):
        with pytest.raises(S.SealError, match="startup_input_contract_incomplete"):
            S.create_new(parent, b"private payload")
    assert S.native_seal_enabled() is False
    report = S.capability_report()
    assert report["status"] == "native_unqualified"
    assert report["input_lease_implementation"] == "retain_only_read_only_from_birth"
    report["native_seal_enabled"] = True
    assert S.native_seal_enabled() is S.capability_report()["native_seal_enabled"] is False


def test_granted_mask_is_observed_and_not_inferred_from_requested_bits(monkeypatch):
    monkeypatch.setattr(
        S, "_native_granted_access", lambda handle: (0, 0x130080, ctypes.sizeof(S._Basic))
    )
    result = S.query_granted_access(123)
    assert result["granted_access"] == 0x130080
    assert result["granted_access"] & S.WRITE_DAC == 0
    assert result["native_seal_enabled"] is False


def test_failed_native_query_never_exports_partially_modified_mask(monkeypatch):
    monkeypatch.setattr(
        S, "_native_granted_access", lambda handle: (-1, 0x1F01FF, ctypes.sizeof(S._Basic))
    )
    result = S.query_granted_access(123)
    assert result["status"] == "incomplete"
    assert result["granted_access"] is None
    assert result["ntstatus"] == -1


def test_short_return_length_never_qualifies_a_mask(monkeypatch):
    monkeypatch.setattr(S, "_native_granted_access", lambda handle: (0, 0x160080, 4))
    result = S.query_granted_access(123)
    assert result["status"] == "incomplete"
    assert result["granted_access"] is None


@pytest.mark.parametrize("handle", [None, 0, -1, True, "123"])
def test_invalid_handle_refuses_before_native_query(monkeypatch, handle):
    monkeypatch.setattr(S, "_native_granted_access", lambda handle: pytest.fail("no native call"))
    with pytest.raises(S.SealError, match="startup_input_contract_incomplete"):
        S.query_granted_access(handle)


def test_win32_error_is_numeric_and_does_not_dump_args_or_endpoint():
    class NativeError(Exception):
        winerror = 5
        funcname = "SetSecurityInfo"

    error = NativeError(5, "SetSecurityInfo", "private path/objref/token content")
    result = S.safe_api_error(error, "SetSecurityInfo")
    assert result == {"function": "SetSecurityInfo", "winerror": 5, "errno": None, "args0": 5}
    assert "private" not in json.dumps(result)
    assert "objref" not in json.dumps(result)
    assert S.safe_api_error(error, "private arbitrary function")["function"] == "native_operation"


def _complete_fixture_facts():
    return {
        "keeper_granted_access": 0x160080,
        **{
            name: True
            for name in (
                "security_only_create_new_succeeded",
                "independent_exclusive_writer_succeeded",
                "actual_full_readback_hash_verified",
                "seal_using_original_keeper_succeeded",
                "sealed_dacl_exact",
                "file_id_unchanged",
                "data_handles_closed",
                "sdk_style_read_share_zero_succeeded",
                "parent_and_ancestors_pinned",
                "namespace_attack_cases_rejected",
            )
        },
        **{name + "_reopen_error": 5 for name in ("write", "delete", "write_dac", "write_owner")},
    }


@pytest.mark.parametrize("attack", ["write", "delete", "write_dac", "write_owner"])
def test_successful_forbidden_open_is_a_failure_even_when_other_steps_pass(attack):
    facts = _complete_fixture_facts()
    facts[attack + "_reopen_error"] = 0
    result = S.evaluate_probe(facts)
    assert result["all_forbidden_reopens_denied_by_acl"] is False
    assert result["protection_pass"] is False
    assert result["native_seal_enabled"] is False


def test_sharing_violation_is_not_acl_denial_and_missing_evidence_is_not_pass():
    facts = _complete_fixture_facts()
    facts["write_reopen_error"] = 32
    assert S.evaluate_probe(facts)["protection_pass"] is False
    facts = _complete_fixture_facts()
    facts.pop("sealed_dacl_exact")
    assert S.evaluate_probe(facts)["protection_pass"] is False
    assert S.evaluate_probe({})["protection_pass"] is False


def test_measured_owner_rights_mask_prevents_claiming_the_proposed_capability():
    facts = _complete_fixture_facts()
    facts["keeper_granted_access"] = 0x130080
    result = S.evaluate_probe(facts)
    assert result["keeper_write_dac_observed_without_data_or_delete"] is False
    assert result["protection_pass"] is False


def test_pure_fixture_happy_facts_do_not_enable_a_product_lease():
    result = S.evaluate_probe(_complete_fixture_facts())
    assert result["protection_pass"] is True
    assert result["scope"] == "fixture_observations_only"
    assert result["native_seal_enabled"] is False
    with pytest.raises(S.SealError, match="startup_input_contract_incomplete"):
        S.create_new("ignored", b"ignored")
