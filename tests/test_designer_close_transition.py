"""Confirmed native lifecycle facts simulated without any native backend/COM."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from designer_identity_fakes import Scene, hard_no_native  # noqa: F401

from xpedition_cli import designer_close_transition as C
from xpedition_cli import native_identity as N
from xpedition_cli import native_release_preflight as R

FACTS = dict(
    save_count=1,
    close_count=1,
    save_acknowledged=True,
    close_returned=True,
    closed_observed=True,
)


def values(tmp_path, framework=False):
    scene = Scene(tmp_path, framework=framework)
    before = scene.identity()
    after = C.expected_after(before)
    return scene, before, after, dict(FACTS)


def check(scene, before, after, facts, **overrides):
    options = dict(
        expected_project=scene.path,
        close_progress=facts,
        same_retained_iunknown=True,
        same_client_thread=True,
    )
    options.update(overrides)
    return C.verify(before, after, scene.rows(), scene.rows(), **options)


@pytest.mark.parametrize("framework", [False, True])
def test_only_explicit_close_document_transition_preserves_raw(tmp_path, framework):
    scene, before, after, facts = values(tmp_path, framework)
    original_before, original_after = deepcopy(before), deepcopy(after)
    result = check(scene, before, after, facts)
    assert result["accepted"] is True
    assert result["before_raw_sha256"] != result["after_raw_sha256"]
    assert before == original_before and after == original_after
    assert result["expected_changed_fields"] == [
        "document.open",
        "document.path",
        "document.designs",
        "designer_window_binding.native_document_binding_sha256",
    ]


@pytest.mark.parametrize("field", list(FACTS))
def test_missing_save_close_return_or_observed_empty_never_permits_transition(tmp_path, field):
    scene, before, after, facts = values(tmp_path)
    del facts[field]
    assert check(scene, before, after, facts)["accepted"] is False


@pytest.mark.parametrize("field", ["same_retained_iunknown", "same_client_thread"])
def test_reference_or_context_changes_rejected(tmp_path, field):
    scene, before, after, facts = values(tmp_path)
    assert check(scene, before, after, facts, **{field: False})["accepted"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_before",
        "still_open",
        "stale_path",
        "designs_remain",
        "digest",
        "hwnd",
        "birth",
        "source",
        "name",
        "extra_doc",
        "child_view",
        "bool_child",
        "extra_identity",
    ],
)
def test_real_changes_incomplete_proof_and_unqualified_child_transitions_rejected(tmp_path, fault):
    scene, before, after, facts = values(tmp_path, True)
    if fault == "foreign_before":
        before["document"]["path"] = str(tmp_path / "foreign.prj")
    elif fault == "still_open":
        after["document"]["open"] = True
    elif fault == "stale_path":
        after["document"]["path"] = scene.path
    elif fault == "designs_remain":
        after["document"]["designs"] = ["PhoneMain"]
    elif fault == "digest":
        after["designer_window_binding"]["native_document_binding_sha256"] = "f" * 64
    elif fault == "hwnd":
        after["designer_window_binding"]["hwnd"] = 456
    elif fault == "birth":
        after["designer_window_binding"]["process_generation"]["birth_filetime_100ns"] = "987654321"
    elif fault == "source":
        after["pid_source"]["property"] = "FullName"
    elif fault == "name":
        after["public_fields"]["Name"] = "different application"
    elif fault == "extra_doc":
        after["document"]["additional"] = "changed"
    elif fault == "child_view":
        before["public_fields"]["ActiveViewHandle"] = 99
    elif fault == "bool_child":
        after["public_fields"]["ActiveViewHandle"] = False
    elif fault == "extra_identity":
        after["additional"] = True
    assert check(scene, before, after, facts)["accepted"] is False


def test_writer_set_change_and_boolean_count_refuse(tmp_path):
    scene, before, after, facts = values(tmp_path)
    assert not C.verify(
        before,
        after,
        scene.rows(),
        [],
        expected_project=scene.path,
        close_progress=facts,
        same_retained_iunknown=True,
        same_client_thread=True,
    )["accepted"]
    facts["save_count"] = True
    assert check(scene, before, after, facts)["accepted"] is False


def gate(monkeypatch, scene):
    scene.install_dependencies(monkeypatch)
    monkeypatch.setattr(R, "_canonical_iunknown", lambda app: scene.app_unknown)
    return R.ReleasePreflight(
        Path(scene.path),
        None,
        process_reader=scene.rows,
        generation_reader=scene.generation_reader,
        application_reader=lambda domain: scene.app,
        identity_reader=N.application_identity,
    )


@pytest.mark.parametrize("framework", [False, True])
def test_real_preflight_class_checks_own_confirmed_close_transition(
    monkeypatch, tmp_path, framework
):
    scene = Scene(tmp_path, framework=framework)
    control = gate(monkeypatch, scene)
    baseline = deepcopy(control.baseline)
    scene.opened = False
    control.confirm_designer_closed(dict(FACTS))
    observed = control.revalidate(closed=("schematic",))
    assert observed["schematic"]["document"] == {
        "open": False,
        "path": None,
        "relation": "empty",
    }
    evidence = control.closed_observations["schematic"]
    assert evidence["formal_identity_transition"]["accepted"] is True
    assert evidence["same_iunknown"] is True
    assert evidence["identity"]["document"]["open"] is False
    assert control.baseline == baseline


def test_preconfirmation_or_unconfirmed_closed_document_stays_strict(monkeypatch, tmp_path):
    scene = Scene(tmp_path)
    control = gate(monkeypatch, scene)
    scene.opened = False
    with pytest.raises(Exception) as failure:
        control.revalidate()
    assert failure.value.code == "E_HUMAN_REQUIRED"
    with pytest.raises(Exception) as failure:
        control.revalidate(closed=("schematic",))
    assert failure.value.code == "E_HUMAN_REQUIRED"


@pytest.mark.parametrize("fault", ["retained_app", "thread", "birth", "foreign_document", "window"])
def test_confirmed_close_cannot_hide_owner_or_document_drift(monkeypatch, tmp_path, fault):
    scene = Scene(tmp_path, framework=True)
    control = gate(monkeypatch, scene)
    scene.opened = False
    control.confirm_designer_closed(dict(FACTS))
    if fault == "retained_app":
        scene.app_unknown = object()
    elif fault == "thread":
        monkeypatch.setattr(R, "threading", SimpleNamespace(get_ident=lambda: control.thread + 1))
    elif fault == "birth":
        scene.generation["birth_filetime_100ns"] = "987654321"
    elif fault == "foreign_document":
        scene.opened = True
        scene.path = str(tmp_path / "other.prj")
    elif fault == "window":
        scene.window["thread_id"] = 99
    with pytest.raises(Exception) as failure:
        control.revalidate(closed=("schematic",))
    assert failure.value.code == "E_HUMAN_REQUIRED"


def test_unexpected_native_read_error_is_redacted_with_phase(monkeypatch, tmp_path):
    scene = Scene(tmp_path)
    scene.install_dependencies(monkeypatch)

    def bad_reader(domain):
        raise ValueError("secret/native-private-path/credential")

    with pytest.raises(Exception) as failure:
        R.ReleasePreflight(
            Path(scene.path),
            None,
            process_reader=bad_reader,
            generation_reader=scene.generation_reader,
            application_reader=lambda domain: scene.app,
            identity_reader=N.application_identity,
        )
    assert "secret/native-private" not in str(failure.value.details)
    assert failure.value.details["stage"] == "release_preflight.inventory_before"
