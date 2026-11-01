"""Pointer comparison only; no CAD, native object or process observation."""

from __future__ import annotations

import copy
import ctypes

import pytest

from xpedition_cli import native_process as P
from xpedition_cli import session
from xpedition_cli import session_ownership as O
from xpedition_cli.backends import NativeBackend
from xpedition_cli.errors import CLIError


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        raise AssertionError("Pointer comparison must not query native applications or processes")

    for name in ("WinDLL", "OleDLL"):
        monkeypatch.setattr(ctypes, name, forbidden, raising=False)
    for target, names in (
        (NativeBackend, ("invoke", "status")),
        (P, ("process_identity", "processes", "ProcessHandle")),
    ):
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    yield
    assert hits == []


@pytest.fixture
def pointers():
    original = {
        "domain": "schematic",
        "process_identity": {
            "pid": 4242,
            "executable": "C:/CAD/WV/WIN64/BIN/ViewDraw.EXE",
            "creation_time_100ns": "134000000000000001",
        },
        "startup_operation_id": "op-original-start",
        "source_sha256": "a" * 64,
    }
    equivalent = copy.deepcopy(original)
    equivalent["process_identity"]["executable"] = "c:/cad/wv/win64/bin/viewdraw.exe"
    return original, equivalent


@pytest.mark.parametrize("source_id", [None, "op-original-start"])
def test_select_returns_original_registry_pointer_for_case_only_readback(
    pointers, monkeypatch, source_id
):
    original, recovered = pointers
    O.register(original)
    before = O.registry_path().read_bytes()
    calls = []

    def validated(domain, actual, operation_id, get_record, validator, scope_id):
        calls.append((domain, actual, operation_id))
        return {"domain": domain, "verified_original_source": True}, copy.deepcopy(recovered)

    monkeypatch.setattr(O, "original_start", validated)
    context, pointer = O.select("schematic", recovered["process_identity"], source_id=source_id)
    assert context["verified_original_source"] is True
    assert pointer == original
    assert pointer != recovered
    assert calls == [("schematic", recovered["process_identity"], original["startup_operation_id"])]
    assert O.registry_path().read_bytes() == before


def test_register_semantically_identical_case_never_rewrites_registry(pointers, monkeypatch):
    original, equivalent = pointers
    O.register(original)
    before = O.registry_path().read_bytes()

    def forbidden_write(_entries):
        pytest.fail("An existing equivalent pointer must retain the original registry bytes")

    monkeypatch.setattr(O, "_write", forbidden_write)
    O.register(equivalent)
    assert O.pointer_for("schematic", equivalent["process_identity"]) == original
    assert O.registry_path().read_bytes() == before


@pytest.mark.parametrize("context", ["same", "other_id", "other_domain", "other_birth", "none"])
def test_only_current_exact_close_context_can_revalidate_its_prepared_state(
    pointers, monkeypatch, context
):
    original, recovered = pointers
    O.register(original)
    session._write_state(
        {
            "state": "closing",
            "domain": "schematic",
            "process_identity": original["process_identity"],
            "closing_operation_id": "op-close-current",
        }
    )
    calls = []

    def validated(*args):
        calls.append(args)
        return {"original_source_verified": True}, recovered

    monkeypatch.setattr(O, "original_start", validated)
    actual = recovered["process_identity"]
    operation_id = "op-close-other" if context == "other_id" else "op-close-current"
    domain = "pcb" if context == "other_domain" else "schematic"
    generation = (
        {**actual, "creation_time_100ns": "134000000000000002"}
        if context == "other_birth"
        else actual
    )
    from contextlib import nullcontext

    manager = (
        nullcontext()
        if context == "none"
        else O.current_close_operation(operation_id, domain, generation)
    )
    with manager:
        if context == "same":
            observed, pointer = O.select("schematic", actual, source_id="op-original-start")
            assert observed["original_source_verified"] is True and pointer == original
        else:
            with pytest.raises(CLIError) as caught:
                O.select("schematic", actual, source_id="op-original-start")
            assert (
                caught.value.details["lifecycle_diagnostic"]["reason"] == "original_close_pending"
            )
    assert bool(calls) is (context == "same")
    with pytest.raises(CLIError):
        O.select("schematic", actual, source_id="op-original-start")


@pytest.mark.parametrize(
    "difference",
    ["exe", "birth", "pid", "source", "operation", "domain", "extra", "identity_extra"],
)
def test_pointer_comparison_refuses_every_non_equivalent_difference(pointers, difference):
    original, altered = pointers
    if difference == "exe":
        altered["process_identity"]["executable"] = "C:/different/viewdraw.exe"
    elif difference == "birth":
        altered["process_identity"]["creation_time_100ns"] = "134000000000000002"
    elif difference == "pid":
        altered["process_identity"]["pid"] += 1
    elif difference == "source":
        altered["source_sha256"] = "b" * 64
    elif difference == "operation":
        altered["startup_operation_id"] = "op-other-start"
    elif difference == "domain":
        altered["domain"] = "pcb"
    elif difference == "extra":
        altered["extra"] = True
    else:
        altered["process_identity"]["extra"] = True
    assert O._pointer_equivalent(original, altered) is False


@pytest.mark.parametrize("source_id", [None, "op-original-start"])
@pytest.mark.parametrize("difference", ["exe", "birth", "source"])
def test_select_real_source_or_generation_changes_still_refuse(
    pointers, monkeypatch, source_id, difference
):
    original, recovered = pointers
    O.register(original)
    before = O.registry_path().read_bytes()
    if difference == "source":
        recovered["source_sha256"] = "b" * 64
    else:
        field, value = (
            ("executable", "C:/other/ViewDraw.exe")
            if difference == "exe"
            else ("creation_time_100ns", "134000000000000002")
        )
        recovered["process_identity"][field] = value
    monkeypatch.setattr(O, "original_start", lambda *args: ({}, recovered))
    with pytest.raises(CLIError):
        # Native observation is already bound to the original pointer. A changed
        # reconstructed source/generation is never substituted for that binding.
        O.select("schematic", original["process_identity"], source_id=source_id)
    assert O.registry_path().read_bytes() == before


@pytest.mark.parametrize("difference", ["source", "operation", "extra"])
def test_register_same_generation_rejects_other_source_without_writing(pointers, difference):
    original, altered = pointers
    O.register(original)
    before = O.registry_path().read_bytes()
    if difference == "source":
        altered["source_sha256"] = "b" * 64
    elif difference == "operation":
        altered["startup_operation_id"] = "op-other-start"
    else:
        altered["extra"] = True
    with pytest.raises(CLIError):
        O.register(altered)
    assert O.registry_path().read_bytes() == before
