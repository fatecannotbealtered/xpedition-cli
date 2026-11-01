"""The Driver derives owned display persistence from two independent full observations."""

from __future__ import annotations

import copy
import os
from types import SimpleNamespace

import pytest
from test_schematic_attribute_display_driver import (
    _design,
    _run,
    no_native_entry,  # noqa: F401
)

from xpedition_cli import driver
from xpedition_cli.schematic_attribute_display import project_display

HOST = "dedicated_startup_host_Application_and_authenticated_ncalrpc"


def _owned(application):
    application["document"]["source"] = HOST
    application["dedicated_startup_binding"] = {
        "source": HOST,
        "canonical_stable": True,
        "original_handle_rechecked": True,
        "connection_epoch_sha256": "a" * 64,
        # These explicitly remain false and are not the source of authority.
        "application_server_pid_observed": False,
        "native_writer_admitted": False,
    }
    application["pid_source"] = {
        "property": HOST,
        "matching_process": {
            "pid": application["pid"],
            "birth_filetime_100ns": "134000000000000001",
            "executable": "C:/fixture/viewdraw.exe",
            "alive": True,
        },
    }


@pytest.fixture
def case(tmp_path):
    project = tmp_path / "owned.prj"
    operation = {
        "type": "set_property",
        "refdes": "R1",
        "name": "Part Label",
        "value": "RC label",
        "display": {"x": 233, "y": 377, "visible": True},
    }
    original = _design()
    expected = copy.deepcopy(original)
    project_display(expected["components"][2], operation)
    application = {
        "pid": 4242,
        "document": {
            "open": True,
            "status": "observed",
            "source": HOST,
            "path": str(project),
            "designs": ["Board1"],
        },
    }
    _owned(application)
    before, after = [
        {
            "id": label,
            "extensions": {"facts": {"application": copy.deepcopy(application), "native": native}},
        }
        for label, native in (("before", original), ("after", copy.deepcopy(expected)))
    ]
    result = {
        "saved": True,
        "save_acknowledged": True,
        "persistence_verified": True,
        "verification": {"valid": True, "persistence_verified": True},
        "read_back_after_reopen": copy.deepcopy(expected),
        "lifecycle": {
            "source": "designer_save_close_reopen",
            "save_count": 1,
            "close_count": 1,
            "reopen_count": 1,
            "save_acknowledged": True,
            "closed_observed": True,
            "close_returned": True,
            "before_pid": 4242,
            "reopened_pid": 4242,
            "before_identity": copy.deepcopy(application["document"]),
            "reopened_identity": copy.deepcopy(application["document"]),
        },
    }
    return SimpleNamespace(
        project=project, before=before, after=after, result=result, operation=operation
    )


def _verify(case):
    return driver._schematic_display_persistence_readback(
        case.result, {"project": str(case.project)}, case.before, case.after, [case.operation]
    )


def test_owned_display_proof_is_derived_without_handler_persistence_alias(case):
    case.result["verification"] = {}
    case.result["persistence_verified"] = False
    evidence = _verify(case)
    assert evidence["valid"] is evidence["complete"] is True
    assert evidence["host_object_binding_verified"] is True
    assert evidence["original_generation_verified"] is True
    assert evidence["identity_verified"] is evidence["lifecycle_verified"] is True
    assert evidence["reopened_snapshot_verified"] is True
    assert evidence["reopened_changes"]["protection_verified"] is True
    assert evidence["reopened_current"]["protection_verified"] is True
    assert evidence["identity_mode"] == "dedicated_startup_owner"


@pytest.mark.parametrize(
    "fault",
    [
        "raw_source_only",
        "missing_binding",
        "source",
        "pid_source",
        "epoch",
        "epoch_missing",
        "epoch_nonhex",
        "canonical",
        "handle",
        "birth",
        "birth_missing",
        "birth_bool",
        "image",
        "image_relative",
        "alive",
        "alive_missing",
        "pid",
        "generation_pid_bool",
        "lifecycle_pid_bool",
        "document_source",
        "document_missing",
        "document_closed",
        "document_status",
        "document_path",
        "document_relative",
        "document_traversing",
        "design",
        "design_duplicate",
        "lifecycle_doc",
        "reopen_count_bool",
        "save_only",
    ],
)
def test_owned_source_or_pid_cannot_replace_complete_host_and_lifecycle_proof(case, fault):
    app = case.after["extensions"]["facts"]["application"]
    binding = app["dedicated_startup_binding"]
    generation = app["pid_source"]["matching_process"]
    document = app["document"]
    if fault == "raw_source_only":
        for snapshot in (case.before, case.after):
            current = snapshot["extensions"]["facts"]["application"]
            current.pop("dedicated_startup_binding")
            current.pop("pid_source")
    elif fault == "missing_binding":
        app.pop("dedicated_startup_binding")
    elif fault == "source":
        binding["source"] = "echo"
    elif fault == "pid_source":
        app["pid_source"]["property"] = "matching-pid-only"
    elif fault == "epoch":
        binding["connection_epoch_sha256"] = "b" * 64
    elif fault == "epoch_missing":
        binding.pop("connection_epoch_sha256")
    elif fault == "epoch_nonhex":
        binding["connection_epoch_sha256"] = "g" * 64
    elif fault == "canonical":
        binding["canonical_stable"] = False
    elif fault == "handle":
        binding["original_handle_rechecked"] = False
    elif fault == "birth":
        generation["birth_filetime_100ns"] = "134000000000000002"
    elif fault == "birth_missing":
        generation.pop("birth_filetime_100ns")
    elif fault == "birth_bool":
        generation["birth_filetime_100ns"] = True
    elif fault == "image":
        generation["executable"] = "C:/other/viewdraw.exe"
    elif fault == "image_relative":
        generation["executable"] = "viewdraw.exe"
    elif fault == "alive":
        generation["alive"] = False
    elif fault == "alive_missing":
        generation.pop("alive")
    elif fault == "pid":
        app["pid"] = 9999
    elif fault == "generation_pid_bool":
        generation["pid"] = True
    elif fault == "lifecycle_pid_bool":
        case.result["lifecycle"]["before_pid"] = True
    elif fault == "document_source":
        document["source"] = "IsProjectOpened/GetProjectData public getters"
    elif fault == "document_missing":
        app.pop("document")
    elif fault == "document_closed":
        document["open"] = False
    elif fault == "document_status":
        document["status"] = "unobserved"
    elif fault == "document_path":
        document["path"] = str(case.project.with_name("foreign.prj"))
    elif fault == "document_relative":
        document["path"] = case.project.name
    elif fault == "document_traversing":
        document["path"] = str(case.project.parent / "folder/../owned.prj")
    elif fault == "design":
        document["designs"] = ["Other"]
    elif fault == "design_duplicate":
        document["designs"] = ["Board1", "Board1"]
    elif fault == "lifecycle_doc":
        case.result["lifecycle"]["reopened_identity"]["path"] = str(
            case.project.with_name("wrong.prj")
        )
    elif fault == "reopen_count_bool":
        case.result["lifecycle"]["reopen_count"] = True
    else:
        case.result = {
            "saved": True,
            "save_acknowledged": True,
            "persistence_verified": True,
            "verification": {"valid": True},
        }
    evidence = _verify(case)
    assert evidence["valid"] is False


@pytest.mark.parametrize("where", ["handler_reopened", "independent_after"])
@pytest.mark.parametrize("fault", ["non_target", "pin_net", "target_style", "missing_collection"])
def test_complete_records_remain_required_on_both_sides_of_reopen(case, where, fault):
    snapshot = (
        case.result["read_back_after_reopen"]
        if where == "handler_reopened"
        else case.after["extensions"]["facts"]["native"]
    )
    if fault == "non_target":
        snapshot["components"][6]["x"] += 10
    elif fault == "pin_net":
        snapshot["components"][6]["pins"][1]["net"] = "CHANGED"
    elif fault == "target_style":
        snapshot["components"][2]["attribute_display"][1]["display"]["origin"] += 1
    else:
        snapshot.pop("unnamed_components")
    evidence = _verify(case)
    assert evidence["valid"] is False
    assert evidence["reopened_snapshot_verified"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows CAD path casing is evaluated on Windows")
def test_case_equivalent_windows_doc_path_uses_the_existing_strict_path_identity(case):
    case.after["extensions"]["facts"]["application"]["document"]["path"] = str(case.project).upper()
    assert _verify(case)["valid"] is True


def test_real_sdk_qualifies_owned_display_from_fresh_records_not_echoed_flags(
    tmp_path, monkeypatch
):
    def flags(result):
        result["verification"] = {}
        result["persistence_verified"] = False

    applied, receipt, _expected = _run(
        tmp_path, monkeypatch, prepare_identity=_owned, mutate_result=flags
    )
    assert applied.envelope["ok"] is True
    assert receipt["outcome"]["persistence"] == "saved"
    assert all(
        row["status"] == "passed"
        for row in receipt["outcome"]["verification"]["checks"]
        if row["required"]
    )
    proof = receipt["extensions"]["file_readback"]["persistence_readback"]
    assert (
        proof["valid"]
        is proof["host_object_binding_verified"]
        is proof["original_generation_verified"]
        is True
    )


@pytest.mark.parametrize("fault", ["birth", "binding"])
def test_real_sdk_retains_unknown_when_host_evidence_is_missing_or_changed(
    tmp_path, monkeypatch, fault
):
    def change(application):
        if fault == "birth":
            application["pid_source"]["matching_process"]["birth_filetime_100ns"] = (
                "134000000000000002"
            )
        else:
            application.pop("dedicated_startup_binding")

    applied, receipt, _expected = _run(
        tmp_path, monkeypatch, prepare_identity=_owned, mutate_identity=change
    )
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["extensions"]["file_readback"]["persistence_verified"] is False
