"""Current 151 raw dispatch contract; no COM, makepy cache or CAD calls."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import native_com_adapter as A


class Declared:
    def __init__(self, iid, methods, calls):
        self.iid, self.methods, self.calls = iid, methods, calls
        self.identity = object()
        self.wrong_member = self.wrong_identity = False

    def QueryInterface(self, iid, wrapped=None):
        if iid == A._IUNKNOWN_IID:
            return object() if self.wrong_identity else self.identity
        assert (iid, wrapped) == (self.iid, A._IDISPATCH_IID)
        return self

    def GetIDsOfNames(self, lcid, name):
        assert lcid == 0
        return self.methods[name][0] + int(self.wrong_member)

    def InvokeTypes(self, dispid, lcid, kind, result_type, arguments, *values):
        selected = [row for row in self.methods.values() if row[0] == dispid]
        assert len(selected) == 1
        _member, result, inputs, operation = selected[0]
        assert (lcid, kind, result_type, arguments) == (0, 1, (result, 0), inputs)
        assert len(values) == len(inputs)
        self.calls.append((dispid, values))
        return operation(*values)


@pytest.fixture
def contract():
    state = NS(calls=[], names=["Board1", "NonFixedName"], count=None, fail_item=False)

    def item(index):
        if state.fail_item and index == 2:
            raise RuntimeError("fixture partial read failure")
        return state.names[index - 1]

    strings = Declared(
        A._STRING_LIST_IID,
        {
            "GetCount": (
                1,
                3,
                (),
                lambda: len(state.names) if state.count is None else state.count,
            ),
            "GetItem": (2, 8, ((3, 1),), item),
        },
        state.calls,
    )
    state.add_reply = True
    data = Declared(
        A._PROJECT_DATA_IID,
        {
            "GetiCDBDesigns": (9, 9, (), lambda: strings),
            "AddiCDBDesign": (36, 11, ((8, 1),), lambda name: state.add_reply),
        },
        state.calls,
    )
    raw = Declared("unused_app_role", {"GetProjectData": (228, 9, (), lambda: data)}, state.calls)

    class DynamicWrapper:
        _oleobj_ = raw

        def __getattr__(self, name):
            pytest.fail("No dynamic attribute, default member, makepy or cache lookup: " + name)

    state.app, state.data, state.strings = DynamicWrapper(), data, strings
    return state


def test_raw_zero_argument_chain_uses_declared_method_types_without_dynamic_wrapper(contract):
    assert A._designer_design_names(contract.app) == ["Board1", "NonFixedName"]
    assert contract.calls == [(228, ()), (9, ()), (1, ()), (2, (1,)), (2, (2,)), (1, ())]


def test_actual_zero_designs_is_observed_not_inferred_from_exception(contract):
    contract.names = []
    assert A._designer_design_names(contract.app) == []
    assert contract.calls == [(228, ()), (9, ()), (1, ()), (1, ())]


def test_changed_count_after_enumeration_cannot_qualify_a_mixed_census(contract):
    counts = iter((2, 1))
    contract.strings.methods["GetCount"] = (1, 3, (), lambda: next(counts))
    with pytest.raises(A.AdapterError, match="census changed"):
        A._designer_design_names(contract.app)
    assert contract.calls == [(228, ()), (9, ()), (1, ()), (2, (1,)), (2, (2,)), (1, ())]


@pytest.mark.parametrize("count", [False, -1, 65, 1.0, "1"])
def test_invalid_counts_do_not_become_empty_or_partial_success(contract, count):
    contract.count = count
    with pytest.raises(A.AdapterError):
        A._designer_design_names(contract.app)
    assert not any(member == 2 for member, _ in contract.calls)


def test_partial_collection_failure_is_not_returned_as_partial_names(contract):
    contract.fail_item = True
    with pytest.raises(RuntimeError, match="partial read failure"):
        A._designer_design_names(contract.app)
    assert contract.calls[-1] == (2, (2,))


@pytest.mark.parametrize("fault", ["wrong_member", "wrong_identity", "duplicate"])
def test_wrong_interface_member_or_ambiguous_names_refuse_without_fallback(contract, fault):
    if fault == "duplicate":
        contract.names = ["Board1", "Board1"]
    else:
        setattr(contract.data, fault, True)
    with pytest.raises(A.AdapterError):
        A._designer_design_names(contract.app)
    assert not any(member == 36 for member, _ in contract.calls)


@pytest.mark.parametrize("reply", [True, False])
def test_add_design_uses_one_explicit_required_bstr_and_exact_bool(contract, reply):
    contract.add_reply = reply
    assert A._designer_add_design(contract.app, "Board1") is reply
    assert contract.calls == [(228, ()), (36, ("Board1",))]


def test_add_design_nonbool_is_not_truthy_acceptance(contract):
    contract.add_reply = 1
    with pytest.raises(A.AdapterError):
        A._designer_add_design(contract.app, "Board1")
    assert contract.calls.count((36, ("Board1",))) == 1
