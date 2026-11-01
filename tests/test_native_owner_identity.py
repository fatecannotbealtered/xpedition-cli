"""Synthetic adapter tests; none activates COM or proves native CAD identity."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

import pytest

from scripts.native_acceptance.owner_identity import capture_identity, compare_identity

APPLICATION_TYPE = {
    "library_guid": "{LIBRARY}",
    "library_name": "MGCPCB",
    "library_major": 151,
    "library_minor": 0,
    "interface_guid": "{APPLICATION}",
    "interface_name": "Application",
}
DOCUMENT_TYPE = {**APPLICATION_TYPE, "interface_guid": "{DOCUMENT}", "interface_name": "Document"}
PROJECT = r"D:\owned\diagnostic-F\PCB\QA_ENTERPRISE.pcb"
EXE = r"D:\vendor\wg\win64\bin\ExpeditionPCB.exe"


@dataclass
class Ref:
    semantic_identity: str
    type_description: dict = field(default_factory=lambda: deepcopy(APPLICATION_TYPE))
    members: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)


class SyntheticAdapter:
    def __init__(self):
        self.document = Ref("document-F", deepcopy(DOCUMENT_TYPE), {"FullName": PROJECT})
        self.gui = Ref("gui-F", members={"HWND": 450})
        self.application = Ref(
            "activation-F",
            members={
                "InstanceGuid": "{ORIGINAL-F}",
                "Gui": self.gui,
                "ActiveDocument": self.document,
            },
        )
        self.observer = Ref(
            "document-app-F",
            members={
                "InstanceGuid": "{OBSERVER-F}",
                "Gui": self.gui,
                "ActiveDocument": self.document,
            },
        )
        self.document.members["Application"] = self.observer
        self.processes = {
            450: {
                "hwnd": 450,
                "pid": 900,
                "birth_filetime_100ns": "134000000000000000",
                "executable": EXE,
                "alive": True,
            }
        }
        self.calls = []

    def read(self, ref, member):
        self.calls.append((ref.semantic_identity, member))
        if member in ref.failures:
            raise ref.failures[member]
        return ref.members[member]

    def describe(self, ref):
        return ref.type_description

    def process(self, hwnd):
        return deepcopy(self.processes[hwnd])

    @staticmethod
    def same_unknown(left, right):
        # Synthetic labels model the boolean from the native QI implementation.
        # No Python identity comparison or pointer is provided to the guard.
        return left.semantic_identity == right.semantic_identity

    def capture(self, sample, *, phase="after_open", document=True, comparer=True):
        return capture_identity(
            self.application,
            self.document if document else None,
            phase=phase,
            sample=sample,
            read_member=self.read,
            describe_type=self.describe,
            observe_process=self.process,
            context_id="same-client-COM-context",
            same_iunknown=self.same_unknown if comparer else None,
        )


def expectations(**extra):
    return {
        "document_path": PROJECT,
        "exe_path": EXE,
        "application_type": APPLICATION_TYPE,
        "document_type": DOCUMENT_TYPE,
        "activation": {
            "pid": 900,
            "birth_filetime_100ns": "134000000000000000",
            "instance_guid": "{ORIGINAL-F}",
        },
        "forbidden_processes": [{"pid": 100, "birth_filetime_100ns": "133000000000000000"}],
        **extra,
    }


def check(adapter, **extra):
    return compare_identity(
        adapter.capture("first"), adapter.capture("second"), expected=expectations(**extra)
    )


def test_original_chain_stable_with_distinct_observer_guid_and_iunknown():
    adapter = SyntheticAdapter()
    first, second = adapter.capture("first"), adapter.capture("second")
    result = compare_identity(first, second, expected=expectations())
    assert result["can_continue_original_chain"]
    assert result["observer_complete"]
    assert not result["errors"]
    assert result["application_references_interchangeable"] == "not_established_by_this_guard"
    assert first["iunknown"]["activation_vs_document_application"]["value"] is False
    assert first["activation_application"]["instance_guid"]["value"] == "{ORIGINAL-F}"
    assert first["document_application"]["instance_guid"]["value"] == "{OBSERVER-F}"
    assert result["native_execution_proved_by_offline_tests"] is False
    assert result["requires_fresh_document_fingerprint"] is False
    assert result["physical_document_fingerprint_verified"] is False


def test_default_rot_c_cannot_substitute_for_owned_activation_f():
    adapter = SyntheticAdapter()
    adapter.application.members["InstanceGuid"] = "{OLD-C}"
    adapter.processes[450].update(pid=100, birth_filetime_100ns="133000000000000000")
    adapter.document.members["FullName"] = r"D:\owned\diagnostic-C\PCB\QA_ENTERPRISE.pcb"
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any(error["reason"] == "forbidden_existing_pid" for error in result["errors"])
    assert any(error["field"].endswith("document_path") for error in result["errors"])


def test_document_named_same_but_actual_wrong_path_is_rejected():
    adapter = SyntheticAdapter()
    adapter.document.members["FullName"] = r"D:\other\PCB\QA_ENTERPRISE.pcb"
    assert not check(adapter)["can_continue_original_chain"]


@pytest.mark.parametrize("member", ["InstanceGuid", "Gui", "ActiveDocument"])
def test_stale_activation_getter_failure_is_explicit(member):
    adapter = SyntheticAdapter()
    adapter.application.failures[member] = RuntimeError("disconnected reference")
    first = adapter.capture("first")
    result = compare_identity(first, adapter.capture("second"), expected=expectations())
    assert not result["can_continue_original_chain"]
    assert "disconnected reference" in str(first)
    assert any(error["reason"] == "unreadable" for error in result["errors"])


@pytest.mark.parametrize(
    "field,new_value", [("pid", 901), ("birth_filetime_100ns", "135000000000000000")]
)
def test_hwnd_reuse_or_process_birth_change_between_same_stage_reads_rejected(field, new_value):
    adapter = SyntheticAdapter()
    first = adapter.capture("first")
    adapter.processes[450][field] = new_value
    result = compare_identity(first, adapter.capture("second"), expected=expectations())
    assert not result["can_continue_original_chain"]
    assert any(error["field"].endswith(f"process.{field}") for error in result["errors"])


def test_pid_reused_with_new_birth_is_not_original_process():
    adapter = SyntheticAdapter()
    adapter.processes[450]["birth_filetime_100ns"] = "135000000000000000"
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any(
        error["field"].endswith("activation.birth_filetime_100ns") for error in result["errors"]
    )


@pytest.mark.parametrize(
    "change",
    ["different_exe", "type_library", "instance_guid_changed", "dead_process"],
)
def test_same_hwnd_does_not_override_other_identity_contradictions(change):
    adapter = SyntheticAdapter()
    first = adapter.capture("first")
    if change == "different_exe":
        adapter.processes[450]["executable"] = r"D:\unexpected\ExpeditionPCB.exe"
    elif change == "type_library":
        adapter.application.type_description["library_major"] = 155
    elif change == "instance_guid_changed":
        adapter.application.members["InstanceGuid"] = "{CHANGED-F}"
    else:
        adapter.processes[450]["alive"] = False
    result = compare_identity(first, adapter.capture("second"), expected=expectations())
    assert not result["can_continue_original_chain"]


def test_observer_wrong_process_is_rejected_even_though_original_chain_is_valid():
    adapter = SyntheticAdapter()
    adapter.observer.members["Gui"] = Ref("other-gui", members={"HWND": 451})
    adapter.processes[451] = {
        "hwnd": 451,
        "pid": 901,
        "birth_filetime_100ns": "135000000000000000",
        "executable": EXE,
        "alive": True,
    }
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any("cross_reference.pid" in error["field"] for error in result["errors"])


def test_cross_observer_failure_preserves_explicit_limited_owner_path():
    adapter = SyntheticAdapter()
    adapter.observer.failures["InstanceGuid"] = RuntimeError("observer GUID unavailable")
    result = check(adapter)
    assert result["can_continue_original_chain"]
    assert not result["observer_complete"]
    assert any("instance_guid" in entry["field"] for entry in result["limitations"])
    assert not check(adapter, require_observer_complete=True)["can_continue_original_chain"]


def test_document_application_getter_failure_does_not_become_same_reference_claim():
    adapter = SyntheticAdapter()
    adapter.document.failures["Application"] = RuntimeError("no observer proxy")
    result = check(adapter)
    assert result["can_continue_original_chain"]
    assert not result["observer_complete"]
    assert len(result["limitations"]) == 4
    assert "no observer proxy" in str(result["limitations"])


def test_readable_observer_with_no_active_document_is_a_real_contradiction():
    adapter = SyntheticAdapter()
    adapter.observer.members["ActiveDocument"] = None
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert result["requires_fresh_document_fingerprint"]
    assert result["physical_document_fingerprint_verified"] is False
    assert any(error["reason"] == "known_document_absent" for error in result["errors"])


def test_observer_same_hwnd_but_wrong_known_type_is_rejected():
    adapter = SyntheticAdapter()
    adapter.observer.type_description["interface_guid"] = "{UNEXPECTED-INTERFACE}"
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any("observer.type.interface_guid" in error["field"] for error in result["errors"])


def test_iunknown_failure_keeps_hresult_without_pointer_or_repr_diagnostics():
    adapter = SyntheticAdapter()

    class Disconnected(RuntimeError):
        hresult = -2147417848

    def failed_qi(left, right):
        raise Disconnected("disconnected canonical IUnknown query")

    first = capture_identity(
        adapter.application,
        adapter.document,
        phase="after_open",
        sample="first",
        read_member=adapter.read,
        describe_type=adapter.describe,
        observe_process=adapter.process,
        context_id="same-client-COM-context",
        same_iunknown=failed_qi,
    )
    result = compare_identity(first, adapter.capture("second"), expected=expectations())
    assert result["can_continue_original_chain"]
    assert result["requires_fresh_document_fingerprint"]
    assert result["physical_document_fingerprint_verified"] is False
    assert (
        first["iunknown"]["activation_active_vs_authenticated"]["failure"]["hresult"] == -2147417848
    )
    assert not result["dimensions"]["com_object_identity"]["satisfied"]
    assert result["dimensions"]["process_identity"]["satisfied"]


@pytest.mark.parametrize("birth", [None, "", "0", 134000000000000000, True, "not-filetime"])
def test_missing_or_invalid_process_birth_is_not_a_stable_generation(birth):
    adapter = SyntheticAdapter()
    adapter.processes[450]["birth_filetime_100ns"] = birth
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any("birth_filetime_100ns" in error["field"] for error in result["errors"])


def test_missing_iunknown_proof_requires_physical_fields_despite_matching_strings():
    adapter = SyntheticAdapter()
    result = compare_identity(
        adapter.capture("first", comparer=False),
        adapter.capture("second", comparer=False),
        expected=expectations(),
    )
    assert result["can_continue_original_chain"]
    assert result["requires_fresh_document_fingerprint"]
    assert result["physical_document_fingerprint_verified"] is False
    assert any(
        item["reason"] == "canonical_document_comparison_unavailable"
        for item in result["limitations"]
    )


@pytest.mark.parametrize("role", ["activation", "observer"])
def test_distinct_cross_role_document_iunknown_requires_independent_physical_fields(role):
    adapter = SyntheticAdapter()
    observed_application = adapter.application if role == "activation" else adapter.observer
    observed_application.members["ActiveDocument"] = Ref(
        "different-COM-document-same-observed-path", deepcopy(DOCUMENT_TYPE), {"FullName": PROJECT}
    )
    result = check(adapter, require_observer_complete=True)
    assert result["can_continue_original_chain"]
    assert result["requires_fresh_document_fingerprint"]
    assert result["physical_document_fingerprint_verified"] is False
    assert len(result["required_physical_document_checks"]) == 2
    assert all(
        item["canonical_iunknown_equal"] is False
        for item in result["required_physical_document_checks"]
    )
    assert any(
        item["reason"] == "different_COM_objects_do_not_decide_physical_document"
        for item in result["limitations"]
    )
    assert result["continuation_scope"].endswith("not_Load_Close_or_write_approval")


def test_distinct_iunknown_with_known_wrong_document_path_still_rejects():
    adapter = SyntheticAdapter()
    adapter.application.members["ActiveDocument"] = Ref(
        "different-COM-document-wrong-path",
        deepcopy(DOCUMENT_TYPE),
        {"FullName": r"D:\owned\diagnostic-C\PCB\QA_ENTERPRISE.pcb"},
    )
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert result["requires_fresh_document_fingerprint"]
    assert any(error["field"].endswith("document_path") for error in result["errors"])


def test_pre_open_and_post_open_are_separate_fresh_stable_phases():
    adapter = SyntheticAdapter()
    adapter.application.members["ActiveDocument"] = None
    pre1, pre2 = (
        adapter.capture("pre-first", phase="pre_open", document=False),
        adapter.capture("pre-second", phase="pre_open", document=False),
    )
    assert compare_identity(pre1, pre2, expected=expectations())["can_continue_original_chain"]
    adapter.application.members["ActiveDocument"] = adapter.document
    post1, post2 = adapter.capture("post-first"), adapter.capture("post-second")
    result = compare_identity(post1, post2, expected=expectations(), previous=pre2)
    assert result["can_continue_original_chain"]
    assert result["phase_changes"] == [
        {"field": "phase", "reason": "transition", "before": "pre_open", "after": "after_open"}
    ]
    assert pre2["activation_application"]["active_document"]["present"] is False
    assert post1["activation_application"]["active_document"]["present"] is True
    assert not compare_identity(pre2, post1, expected=expectations())["can_continue_original_chain"]


def test_post_close_empty_is_fresh_stable_and_preserves_original_process_chain():
    adapter = SyntheticAdapter()
    before_close = adapter.capture("before-close")
    adapter.application.members["ActiveDocument"] = None
    first = adapter.capture("closed-first", phase="post_close", document=False)
    second = adapter.capture("closed-second", phase="post_close", document=False)
    result = compare_identity(first, second, expected=expectations(), previous=before_close)
    assert result["can_continue_original_chain"]
    assert result["dimensions"]["process_identity"]["satisfied"]
    assert first["activation_application"]["active_document"]["present"] is False
    assert result["phase_changes"][-1] == {
        "field": "phase",
        "reason": "transition",
        "before": "after_open",
        "after": "post_close",
    }


def test_post_open_missing_document_is_still_rejected():
    adapter = SyntheticAdapter()
    adapter.application.members["ActiveDocument"] = None
    first = adapter.capture("first", phase="post_open", document=False)
    second = adapter.capture("second", phase="post_open", document=False)
    result = compare_identity(first, second, expected=expectations())
    assert not result["can_continue_original_chain"]
    assert any(error["reason"] == "expected_document_absent" for error in result["errors"])


def test_post_close_existing_active_or_authenticated_document_is_rejected():
    adapter = SyntheticAdapter()
    first = adapter.capture("first", phase="post_close", document=True)
    second = adapter.capture("second", phase="post_close", document=True)
    result = compare_identity(first, second, expected=expectations())
    assert not result["can_continue_original_chain"]
    assert any(
        error["reason"] == "empty_document_phase_must_be_absent" for error in result["errors"]
    )


def test_birth_changed_across_phase_cannot_be_hidden_by_two_stable_post_reads():
    adapter = SyntheticAdapter()
    adapter.application.members["ActiveDocument"] = None
    pre = adapter.capture("pre", phase="pre_open", document=False)
    adapter.application.members["ActiveDocument"] = adapter.document
    adapter.processes[450]["birth_filetime_100ns"] = "135000000000000000"
    result = compare_identity(
        adapter.capture("post-first"),
        adapter.capture("post-second"),
        expected=expectations(activation={"pid": 900}),
        previous=pre,
    )
    assert not result["can_continue_original_chain"]
    assert any(
        error["field"] == "phase_continuity.birth_filetime_100ns" for error in result["errors"]
    )


def test_same_snapshot_twice_or_changed_thread_context_is_not_a_second_observation():
    adapter = SyntheticAdapter()
    first = adapter.capture("first")
    assert not compare_identity(first, first, expected=expectations())[
        "can_continue_original_chain"
    ]
    second = adapter.capture("second")
    second["client_thread_id"] += 1
    assert not compare_identity(first, second, expected=expectations())[
        "can_continue_original_chain"
    ]
    second = adapter.capture("second")
    second["context_id"] = "another-COM-context"
    assert not compare_identity(first, second, expected=expectations())[
        "can_continue_original_chain"
    ]


def test_hwnd_to_process_binding_itself_must_be_consistent():
    adapter = SyntheticAdapter()
    adapter.processes[450]["hwnd"] = 999
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any(error["field"].endswith(".hwnd") for error in result["errors"])


def test_windows_paths_compare_normalized_without_weak_filename_matching():
    adapter = SyntheticAdapter()
    adapter.document.members["FullName"] = PROJECT.lower().replace("\\", "/")
    adapter.processes[450]["executable"] = EXE.lower().replace("\\", "/")
    assert check(adapter)["can_continue_original_chain"]


def test_invalid_type_result_and_unavailable_process_are_not_silently_empty():
    adapter = SyntheticAdapter()
    adapter.application.type_description = {}
    adapter.processes[450] = {}
    result = check(adapter)
    assert not result["can_continue_original_chain"]
    assert any(error["field"].endswith("library_guid") for error in result["errors"])
    assert any(
        error["field"].endswith("process.birth_filetime_100ns") for error in result["errors"]
    )


def test_only_injected_formal_read_members_are_used():
    adapter = SyntheticAdapter()
    assert check(adapter)["can_continue_original_chain"]
    assert {member for _, member in adapter.calls} == {
        "InstanceGuid",
        "Gui",
        "HWND",
        "ActiveDocument",
        "FullName",
        "Application",
    }
