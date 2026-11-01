"""Windows path spelling alone is not a different native PDF source."""

import copy
import os

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import driver as D
from xpedition_cli import pdf_source_identity, schematic_pdf_artifacts


def test_planning_and_protected_pdf_readback_share_the_exact_path_predicate():
    assert D._pdf_same_project_path is pdf_source_identity.same_project_path
    assert schematic_pdf_artifacts.same_project_path is pdf_source_identity.same_project_path


def captured(path=r"D:\Authorized\PhoneTrial\Board.prj"):
    return {
        "extensions": {
            "facts": {
                "application": {
                    "pid": 4242,
                    "document": {"open": True, "status": "observed", "path": path},
                },
                "native": {
                    "components": [
                        {
                            "uid": "$1I1",
                            "pins": [],
                            "identity_observation_coverage": {"complete": True, "errors": []},
                            "pin_observation_coverage": {"complete": True, "errors": []},
                        }
                    ],
                    "unnamed_components": [],
                    "sheets": [{"number": 1}],
                    "nets": [],
                    "connections": [],
                    "metadata": {"backend": "native_xpedition", "source": "Viewdraw.Application"},
                },
            }
        },
    }


@pytest.mark.parametrize(
    "observed",
    [
        r"d:\authorized\phonetrial\board.prj",
        "d:/AUTHORIZED/PhoneTrial/BOARD.PRJ",
        r"D:\Authorized/PHONETRIAL\Board.prj",
    ],
)
def test_windows_case_and_separator_spelling_reuses_existing_owner_key(observed, monkeypatch):
    from xpedition_cli import designer_owner_dispatch as O

    key = O._project_key
    calls = []
    monkeypatch.setattr(O, "_project_key", lambda path: calls.append(path) or key(path))
    value = captured(observed)
    before = copy.deepcopy(value)
    assert D._pdf_source_complete(value, r"D:\Authorized\PhoneTrial\Board.prj")
    assert len(calls) == 2 and value == before


@pytest.mark.parametrize(
    "observed",
    [
        r"D:\Authorized\Other\Board.prj",
        r"D:\Authorized\PhoneTrial\Board.prj.bak",
        r"D:\Authorized\PhoneTrialExtra\Board.prj",
        r"D:\Authorized\PhoneTrial\Child\..\Board.prj",
        None,
        "",
        "Board.prj",
        "D:Board.prj",
        r"\Authorized\PhoneTrial\Board.prj",
    ],
)
def test_foreign_ambiguous_or_nonabsolute_paths_do_not_qualify(observed):
    assert not D._pdf_source_complete(captured(observed), r"D:\Authorized\PhoneTrial\Board.prj")


@pytest.mark.parametrize(
    "fault",
    [
        "pid",
        "closed",
        "status",
        "no_document",
        "source",
        "backend",
        "truncated",
        "incomplete",
        "missing_list",
        "empty_sheets",
        "missing_uid",
        "duplicate_uid",
        "missing_pins",
        "identity_coverage",
        "pin_coverage",
        "getter_error",
    ],
)
def test_spelling_normalization_cannot_replace_complete_source_observations(fault):
    value = captured(r"d:\authorized\phonetrial\board.prj")
    facts = value["extensions"]["facts"]
    app, native = facts["application"], facts["native"]
    part = native["components"][0]
    if fault == "pid":
        app["pid"] = True
    elif fault == "closed":
        app["document"]["open"] = False
    elif fault == "status":
        app["document"].pop("status")
    elif fault == "no_document":
        app.pop("document")
    elif fault in {"source", "backend"}:
        native["metadata"].pop(fault)
    elif fault == "truncated":
        native["metadata"]["truncated"] = True
    elif fault == "incomplete":
        native["metadata"]["complete"] = False
    elif fault == "missing_list":
        native.pop("connections")
    elif fault == "empty_sheets":
        native["sheets"] = []
    elif fault == "missing_uid":
        part.pop("uid")
    elif fault == "duplicate_uid":
        native["components"].append(copy.deepcopy(part))
    elif fault == "missing_pins":
        part.pop("pins")
    elif fault == "identity_coverage":
        part["identity_observation_coverage"]["complete"] = False
    elif fault == "pin_coverage":
        part["pin_observation_coverage"]["complete"] = False
    else:
        part["pin_observation_coverage"]["errors"] = ["getter failed"]
    assert not D._pdf_source_complete(value, r"D:\Authorized\PhoneTrial\Board.prj")


def test_local_posix_fixture_paths_do_not_gain_windows_case_insensitivity(tmp_path):
    if os.name == "nt":
        pytest.skip("Windows path semantics are covered above")
    assert not D._pdf_source_complete(
        captured(str(tmp_path / "Other.prj")), str(tmp_path / "other.prj")
    )
