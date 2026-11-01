"""PDF command side records are authenticated resources, never CAD qualification."""

from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import os
import shutil
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_owner_capacity_lifecycle import _client, _context, _submit

from xpedition_cli import designer_driver_routing as R
from xpedition_cli import designer_frozen_binding as F
from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_client as C
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli.errors import CLIError
from xpedition_cli.schematic_pdf_artifacts import file_pin

OPERATION = "op-pdf-record-fixture"


def _publish(scene, monkeypatch, *, native_binary=False):
    home = scene.root.parent / "fixture-runtime"
    executable = home / "common/win64/bin/sch2pdf.exe"
    executable.parent.mkdir(parents=True)
    if native_binary:
        # This is the installed Windows command processor copied into an owned
        # temporary directory, not Siemens code or a new installed component.
        shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32/cmd.exe", executable)
    else:
        executable.write_bytes(b"declared non-executable protocol fixture")
    project = Path(scene.project)
    project.parent.mkdir(exist_ok=True)
    project.write_bytes(b"non-CAD engineering identity fixture")
    scene.application.project = scene.project
    scene.application._capacity_document_empty = False
    expected = {
        "generation_id": OPERATION,
        "project": file_pin(project),
        "producer": file_pin(executable),
        "output": str(scene.root.parent / "fixture.pdf"),
        "color": 1,
        "schematic": None,
    }
    params = {
        "project": scene.project,
        "output": expected["output"],
        "artifact_generation": OPERATION,
        "_pdf_expectation": copy.deepcopy(expected),
    }
    binding = {
        "command": "schematic export",
        "scope_id": scene.intent["editing_scope"]["scope_id"],
        "scope_sha256": scene.intent["editing_scope"]["scope_sha256"],
        "source_operation_id": OPERATION,
        "pdf_export": expected,
    }
    observed = O.observe_owner_command
    monkeypatch.setattr(
        O,
        "observe_owner_command",
        lambda _ticket, command_id, **_: {"command_id": command_id, "status": "unknown"},
    )
    try:
        reply = O.submit_owner_adapter_command(
            scene.ticket,
            "export_pdf",
            params,
            sdk_operation_id=OPERATION,
            authorization_binding=binding,
            observation_seconds=0,
        )
    finally:
        monkeypatch.setattr(O, "observe_owner_command", observed)
    directory = scene.root / "commands" / reply["command_id"]
    request, request_sha256 = O._command_read(directory / "request.json", scene.key)
    writer = O._CommandJournal(directory, scene.key, request)
    writer.append("command_received", method="export_pdf")
    writer.append("command_started", method="export_pdf")
    return NS(
        scene=scene,
        home=home,
        executable=executable,
        directory=directory,
        request=request,
        request_sha256=request_sha256,
        writer=writer,
        params=params,
        binding=binding,
    )


def _terminal(frame):
    frame.writer.append(
        "command_returned",
        result={"exported": True, "fixture_only": True},
        owner_admission=O._capacity_owner_snapshot(
            frame.scene.application,
            frame.scene.ticket.epoch,
            {"pid": 7777, "birth_filetime_100ns": "100", "executable": "C:/vendor/viewdraw.exe"},
            len(list((frame.scene.root / "commands").iterdir())),
        ),
    )


def _synthetic(frame, *, terminal=True, returned=True, image=True, alive=True):
    """Adversarial protocol fixtures; the separate real-child tests prove shape."""
    logs = (b"Printed RCFixture Sheet 1\r\n", b"")
    for name, data in zip(O.PDF_PRODUCER_LOGS, logs, strict=True):
        (frame.directory / name).write_bytes(data)
    initial = {
        "command_id": frame.request["command_id"],
        "epoch": frame.request["epoch"],
        "original_outcome": "unknown",
        "immutable": True,
        "expected_producer_path": ntpath.normcase(ntpath.normpath(str(frame.executable.resolve()))),
        "request_sha256": frame.request_sha256,
    }
    launched = {
        "command_id": frame.request["command_id"],
        "epoch": frame.request["epoch"],
        "process": {
            "pid": 12345,
            "birth_filetime_100ns": "200",
            "executable": str(frame.executable.resolve()) if image else None,
            "alive": alive,
        },
        "automatic_replay": False,
    }
    complete = {
        "command_id": frame.request["command_id"],
        "epoch": frame.request["epoch"],
        "process_returned": True,
        "exit_code": 0,
        "resource_cleanup_complete": True,
        "stdout_bytes": len(logs[0]),
        "stderr_bytes": len(logs[1]),
        "stdout_sha256": hashlib.sha256(logs[0]).hexdigest(),
        "stderr_sha256": hashlib.sha256(logs[1]).hexdigest(),
    }
    records = {"pdf-producer-initial.json": initial, "pdf-producer-launched.json": launched}
    if returned:
        records["pdf-producer-returned.json"] = complete
    for name, payload in records.items():
        (frame.directory / name).write_bytes(O.L._pack(payload, frame.scene.key))
    if terminal:
        _terminal(frame)
    return frame


def _real(frame, monkeypatch, *, wait_before_identity=False):
    owner = O.NativeApplication.__new__(O.NativeApplication)
    owner._owner = lambda: None
    owner._dual_count_probe = owner._dual_document_call = None
    owner._pdf_producer = None
    owner.runtime_root = frame.scene.root
    owner.boundary = NS(command_id=frame.request["command_id"])
    owner._origin_handoff = NS(_epoch=frame.scene.ticket.epoch)
    owner.lease = NS(home=frame.home)
    monkeypatch.setattr(F, "external_application_dll_search", nullcontext)
    monkeypatch.setattr(F, "external_child_environment", lambda environment: dict(environment))
    original_spawn = O.subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = original_spawn(*args, **kwargs)
        children.append(child)
        if wait_before_identity:
            child.wait(timeout=5)  # Genuine natural exit; original HANDLE is still retained.
        return child

    monkeypatch.setattr(O.subprocess, "Popen", spawn)
    result = owner.run_pdf_producer(
        [str(frame.executable), "/d", "/c", "echo Printed RCFixture Sheet 1"],
        frame.executable.parent,
    )
    assert result["returncode"] == 0 and result["owned_producer_terminal"] is True
    assert len(children) == 1 and children[0].returncode == 0
    assert children[0]._handle is None and owner._pdf_producer is None
    _terminal(frame)
    return frame


@pytest.mark.skipif(os.name != "nt", reason="Real Windows Popen HANDLE/provenance test")
@pytest.mark.parametrize("fast_exit", [False, True])
def test_real_owned_producer_records_allow_c_to_o_readback_and_lifecycle_capacity(
    tmp_path, monkeypatch, fast_exit
):
    monkeypatch.setattr(
        O, "MAX_PUBLIC_COMMANDS", 27
    )  # Three ordinary slots, unchanged 3x8 reserves.
    scene = _context(monkeypatch, tmp_path)
    frame = _real(
        _publish(scene, monkeypatch, native_binary=True),
        monkeypatch,
        wait_before_identity=fast_exit,
    )
    original = {path.name: path.read_bytes() for path in frame.directory.iterdir()}
    assert len(original) == 10
    launched, _ = O._command_read(frame.directory / "pdf-producer-launched.json", scene.key)
    if fast_exit:
        assert launched["process"]["alive"] is False
    report = O.observe_owner_capacity(scene.ticket)
    assert report["status"] == "observed"
    assert report["bytes"]["used"] == sum(len(raw) for raw in original.values())
    assert report["bytes"]["committed_including_pending"] == report["bytes"]["used"]
    for method in ("identity", "snapshot"):
        locator = _client(
            monkeypatch,
            scene,
            operation="query-after-pdf",
            binding={
                "command": "schematic parts",
                "scope_id": scene.intent["editing_scope"]["scope_id"],
                "scope_sha256": scene.intent["editing_scope"]["scope_sha256"],
                "source_operation_id": "query-after-pdf",
            },
        )
        assert (
            C.call_owner(locator, method, {"project": scene.project, "observe_only": True}, 10)[
                "fixture"
            ]
            is True
        )
    scope = D.validate_editing_scope(scene.intent["editing_scope"])
    checkpoint = R._binding(
        scope,
        "project backup",
        "op-checkpoint",
        options={
            "project": scene.project,
            "output": scene.archive,
            "leave-closed": True,
        },
    )
    locator = _client(monkeypatch, scene, operation="op-checkpoint", binding=checkpoint)
    C.call_owner(
        locator, "release_project_preflight", {"project": scene.project, "observe_only": True}, 10
    )
    C.call_owner(locator, "release_project", {"project": scene.project}, 10)
    quit_binding = R._binding(scope, "session close", "op-quit", options={"pid": 7777})
    locator = _client(monkeypatch, scene, operation="op-quit", binding=quit_binding)
    C.call_owner(locator, "identity", {"domain": "schematic"}, 10)
    C.call_owner(locator, "close", {"domain": "schematic"}, 10)
    capacity = O.observe_owner_capacity(scene.ticket)
    assert capacity["reserve_claimed"]["closed_checkpoint"] is True
    assert capacity["reserve_claimed"]["empty_quit"] is True
    assert {path.name: path.read_bytes() for path in frame.directory.iterdir()} == original
    assert json.loads(original["initial.json"])["payload"]["original_outcome"] == "unknown"
    # C/O publication and a real non-CAD producer are exercised. No Designer,
    # CAD identity, project save or actual CAD Quit is qualified by these fixtures.


@pytest.mark.parametrize(
    "fault",
    [
        "mac",
        "epoch",
        "command_id",
        "request_hash",
        "expected_path",
        "wrong_method",
        "unknown_file",
        "log_hash",
        "log_size",
        "log_oversize",
        "metadata_oversize",
        "null_alive",
        "null_without_returned",
        "cleanup_false",
        "extra_metadata_field",
    ],
)
def test_pdf_side_record_corruption_refuses_new_requests_without_rewriting_history(
    tmp_path, monkeypatch, fault
):
    scene = _context(monkeypatch, tmp_path)
    frame = _synthetic(_publish(scene, monkeypatch))
    target = frame.directory / "pdf-producer-initial.json"
    if fault == "mac":
        data = json.loads(target.read_bytes())
        data["mac"] = "0" * 64
        target.write_text(json.dumps(data), encoding="utf-8")
    elif fault in {"epoch", "command_id", "request_hash", "expected_path", "extra_metadata_field"}:
        data, _ = O._command_read(target, scene.key)
        if fault == "epoch":
            data["epoch"] = "b" * 32
        elif fault == "command_id":
            data["command_id"] = "owner-command-" + "d" * 32
        elif fault == "request_hash":
            data["request_sha256"] = "0" * 64
        elif fault == "expected_path":
            data["expected_producer_path"] += ".other"
        else:
            data["unrecognized"] = True
        target.write_bytes(O.L._pack(data, scene.key))
    elif fault == "wrong_method":
        target = frame.directory / "request.json"
        data, _ = O._command_read(target, scene.key)
        data["method"] = "identity"
        target.write_bytes(O.L._pack(data, scene.key))
    elif fault == "unknown_file":
        (frame.directory / "unknown-side-file.bin").write_bytes(b"must not be ignored")
    elif fault in {"log_hash", "log_size", "log_oversize"}:
        original_log = (frame.directory / O.PDF_PRODUCER_LOGS[0]).read_bytes()
        (frame.directory / O.PDF_PRODUCER_LOGS[0]).write_bytes(
            b"x" * (O.MAX_PDF_PRODUCER_LOG_BYTES + 1)
            if fault == "log_oversize"
            else bytes([original_log[0] ^ 1]) + original_log[1:]
            if fault == "log_hash"
            else b"changed log"
        )
    elif fault == "metadata_oversize":
        target.write_bytes(target.read_bytes() + b" " * O.MAX_COMMAND_RECORD_BYTES)
    else:
        target = frame.directory / (
            "pdf-producer-launched.json"
            if fault.startswith("null_")
            else "pdf-producer-returned.json"
        )
        data, _ = O._command_read(target, scene.key)
        if fault.startswith("null_"):
            data["process"].update(executable=None, alive=fault == "null_alive")
            if fault == "null_without_returned":
                (frame.directory / "pdf-producer-returned.json").unlink()
        else:
            data["resource_cleanup_complete"] = False
        target.write_bytes(O.L._pack(data, scene.key))
    original = {path.name: path.read_bytes() for path in frame.directory.iterdir()}
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(scene, operation="query-new")
    assert len(list((scene.root / "commands").iterdir())) == 1
    assert {path.name: path.read_bytes() for path in frame.directory.iterdir()} == original


def test_null_exited_image_is_resource_only_and_requires_complete_returned_binding(
    tmp_path, monkeypatch
):
    scene = _context(monkeypatch, tmp_path)
    _synthetic(_publish(scene, monkeypatch), image=False, alive=False)
    report = O.observe_owner_capacity(scene.ticket)
    assert report["status"] == "observed" and report["authorization_granted"] is False
    assert report["native_observation_performed"] is False


@pytest.mark.parametrize("metadata_complete", [False, True])
def test_pending_pdf_reserves_all_metadata_logs_and_future_terminal(
    tmp_path, monkeypatch, metadata_complete
):
    scene = _context(monkeypatch, tmp_path)
    frame = _publish(scene, monkeypatch)
    if metadata_complete:
        _synthetic(frame, terminal=False)
    report = O.observe_owner_capacity(scene.ticket)
    assert report["status"] == "observed"
    assert report["bytes"]["committed_including_pending"] == O.MAX_PDF_COMMAND_SLOT_BYTES
    assert O.MAX_PDF_COMMAND_SLOT_BYTES == 7 * 1024**2 + O.MAX_COMMAND_AUX_BYTES


@pytest.mark.parametrize("delta,accepted", [(-1, False), (0, True)])
def test_pdf_publication_budget_is_method_specific_before_any_directory(
    tmp_path, monkeypatch, delta, accepted
):
    total_reserved = sum(O._capacity_pools().values()) * O.MAX_COMMAND_SLOT_BYTES
    monkeypatch.setattr(
        O, "MAX_PUBLIC_BYTES", total_reserved + O.MAX_PDF_COMMAND_SLOT_BYTES + delta
    )
    scene = _context(monkeypatch, tmp_path)
    if accepted:
        _publish(scene, monkeypatch)
        assert len(list((scene.root / "commands").iterdir())) == 1
    else:
        with pytest.raises(O.OwnerCommandNotPublished, match="verification_budget_reserved"):
            _publish(scene, monkeypatch)
        assert list((scene.root / "commands").iterdir()) == []


def test_pending_auxiliary_raw_bytes_cannot_exceed_the_fixed_metadata_allowance(
    tmp_path, monkeypatch
):
    scene = _context(monkeypatch, tmp_path)
    frame = _publish(scene, monkeypatch)
    path = frame.directory / "initial.json"
    path.write_bytes(path.read_bytes() + b" " * O.MAX_COMMAND_AUX_BYTES)
    O._command_read(path, scene.key)  # Whitespace is still a valid MAC packet.
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(scene, operation="query-new")


def test_log_change_during_opened_read_is_rejected_by_file_identity_snapshot(tmp_path, monkeypatch):
    scene = _context(monkeypatch, tmp_path)
    _synthetic(_publish(scene, monkeypatch))
    original_read = O._capacity_pdf_bytes

    def changed(path, maximum, before, **kwargs):
        value = original_read(path, maximum, before, **kwargs)
        if path.name == O.PDF_PRODUCER_LOGS[0]:
            with path.open("ab") as stream:
                stream.write(b"changed after the bounded read")
        return value

    monkeypatch.setattr(O, "_capacity_pdf_bytes", changed)
    with pytest.raises(O.OwnerCommandNotPublished, match="record_unstable"):
        _submit(scene, operation="query-new")


def test_oversized_log_is_rejected_before_opening_its_contents(tmp_path, monkeypatch):
    scene = _context(monkeypatch, tmp_path)
    frame = _synthetic(_publish(scene, monkeypatch))
    path = frame.directory / O.PDF_PRODUCER_LOGS[0]
    path.write_bytes(b"x" * (O.MAX_PDF_PRODUCER_LOG_BYTES + 1))
    original_open = Path.open

    def bounded_open(candidate, *args, **kwargs):
        if candidate == path:
            pytest.fail("An oversized producer log was read")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", bounded_open)
    locator = _client(monkeypatch, scene)
    with pytest.raises(CLIError) as failure:
        C.call_owner(locator, "identity", {"domain": "schematic"}, 10)
    assert failure.value.details["reason"] == "public_owner_pdf_log_limit"
    assert failure.value.details["owner_request_published"] is False
