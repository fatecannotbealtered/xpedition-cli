"""Real authenticated file admission; all application/process facts are fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import designer_driver_routing as R
from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_client as C
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli.errors import CLIError

ROOT = Path(__file__).resolve().parents[1]


def _context(monkeypatch, temporary, *, complete=True):
    root = temporary / "owner"
    root.mkdir(exist_ok=True)
    (root / "commands").mkdir(exist_ok=True)
    key = b"k" * 32
    ticket = O.OwnerTicket(root, "a" * 32)
    scope_path = temporary / "scope.json"
    scope_record = {
        "scope_id": "owner-capacity-fixture",
        "tool": "xpedition-cli",
        "granted_by": "offline fixture",
        "granted_at": "2026-10-06T00:00:00Z",
        "expires_at": None,
        "effects": ["observe", "session", "persist", "change", "artifact"],
        "dangerous": False,
        "roots": [str(temporary.resolve())],
        "outputs": [str(temporary.resolve())],
        "destinations": [],
    }
    scope_path.write_text(json.dumps(scope_record), encoding="utf-8")
    editing_scope = {
        "scope_id": scope_record["scope_id"],
        "scope_file": str(scope_path.resolve()),
        "scope_sha256": hashlib.sha256(scope_path.read_bytes()).hexdigest(),
    }
    intent = {"mode": O.PUBLIC_MODE, "source_sha256": {}, "editing_scope": editing_scope}
    generation = {
        "pid": 7777,
        "birth_filetime_100ns": "100",
        "executable": "C:/vendor/viewdraw.exe",
    }
    application = NS(project=None, _capacity_document_empty=True)
    ready = O._capacity_owner_snapshot(application, ticket.epoch, generation, 0)
    evidence = root / "native-launch-evidence.json"
    if not evidence.exists():
        O.L._write_new(evidence, O.L._pack({"expected_native_generation": generation}, key))
    monkeypatch.setattr(O, "_context", lambda *_: (root, key, intent))
    monkeypatch.setattr(O, "_source_binding", lambda: {})
    monkeypatch.setattr(O, "_registration", lambda *_: {})
    monkeypatch.setattr(O.L, "_fresh_owner", lambda *_: ("verified_alive", {}))
    monkeypatch.setattr(
        O.L,
        "_events",
        lambda *_: [{"phase": "public_dispatch_ready", "details": {"owner_admission": ready}}],
    )
    monkeypatch.setattr(O.L, "_plain_path", lambda p: Path(p))
    monkeypatch.setattr(O.L, "_private_mkdir", lambda p: p.mkdir())
    monkeypatch.setattr(O.L, "_launch_lock", lambda *_: nullcontext())
    responses = {}

    def observed(_ticket, command_id, **_kwargs):
        directory = root / "commands" / command_id
        request, _ = O._command_read(directory / "request.json", key)
        if complete:
            if request["method"] == "clone_project":
                application.project = request["params"]["project"]
                application._capacity_document_empty = False
            elif request["method"] == "release_project":
                application._capacity_document_empty = True
            writer = O._CommandJournal(directory, key, request)
            writer.append("command_received", method=request["method"])
            writer.append("command_started", method=request["method"])
            writer.append(
                "command_returned",
                result=copy.deepcopy(responses.get(request["method"], {"fixture": True})),
                owner_admission=O._capacity_owner_snapshot(
                    application, ticket.epoch, generation, len(list((root / "commands").iterdir()))
                ),
            )
        return {
            "command_id": command_id,
            "status": "completed" if complete else "unknown",
            "observations": O._public_command_observations(directory, key, request),
        }

    monkeypatch.setattr(O, "observe_owner_command", observed)
    return NS(
        root=root,
        key=key,
        ticket=ticket,
        intent=intent,
        project=str((temporary / "project" / "own.prj").resolve()),
        archive=str((temporary / "checkpoint.zip").resolve()),
        application=application,
        responses=responses,
    )


def _submit(
    scene,
    method="identity",
    *,
    operation="query-current",
    command="schematic parts",
    lifecycle=None,
    params=None,
):
    binding = {
        "command": command,
        "source_operation_id": operation,
        "scope_id": scene.intent["editing_scope"]["scope_id"],
        "scope_sha256": scene.intent["editing_scope"]["scope_sha256"],
    }
    if lifecycle:
        binding["lifecycle_intent"] = {"scheme": "designer-owner-lifecycle-v1", "kind": lifecycle}
        binding["lifecycle_intent"].update(
            {"pid": 7777}
            if lifecycle == "empty_quit"
            else {"project": scene.project, "output": scene.archive}
        )
    if params is None:
        params = (
            {"project": scene.project, "observe_only": True}
            if method
            in {"snapshot", "release_project_preflight", "release_project", "clone_project"}
            else {}
        )
    return O.submit_owner_adapter_command(
        scene.ticket,
        method,
        params,
        sdk_operation_id=operation,
        authorization_binding=binding,
        observation_seconds=0,
    )


def _small(monkeypatch):
    monkeypatch.setattr(O, "MAX_PUBLIC_COMMANDS", 10)
    monkeypatch.setattr(O, "VERIFICATION_COMMAND_RESERVE", 2)
    monkeypatch.setattr(O, "CHECKPOINT_COMMAND_RESERVE", 2)
    monkeypatch.setattr(O, "QUIT_COMMAND_RESERVE", 2)


def test_default_contract_preserves_finite_total_and_separate_reserves():
    contract = O.capacity_contract()
    assert contract["maximum_commands"] == 128
    assert contract["maximum_bytes"] == 128 * 1024**2
    assert contract["reserves"] == {"verification": 8, "closed_checkpoint": 8, "empty_quit": 8}
    assert contract["ordinary_command_slot_ceiling"] == 104
    assert contract["capacity_is_authorization"] is False
    assert contract["byte_limit_can_reduce_available_slots"] is True


def test_saturated_default_session_preserves_original_verification_checkpoint_and_quit(
    tmp_path, monkeypatch
):
    scene = _context(monkeypatch, tmp_path)
    _submit(scene, "clone_project", operation="op-edit", command="project create")
    for index in range(103):
        _submit(scene, operation=f"query-{index}")
    original = {p: p.read_bytes() for p in (scene.root / "commands").rglob("initial.json")}
    with pytest.raises(O.OwnerCommandNotPublished, match="verification_budget_reserved") as refusal:
        _submit(scene, operation="query-one-more")
    assert refusal.value.capacity["ordinary_commands_available"] == 0
    assert len(list((scene.root / "commands").iterdir())) == 104
    for method in ("snapshot", "identity"):
        _submit(
            scene,
            method,
            operation="op-edit",
            command="project create",
            params={"observe_only": True},
        )
    # Exact owner-command counts from the successful native17 public flow:
    # leave-closed backup = 4 preflight observations + one release; Quit =
    # five identity observations + one close. No native action occurs here.
    for method in ["release_project_preflight"] * 4 + ["release_project"]:
        _submit(
            scene,
            method,
            operation="op-checkpoint",
            command="project backup",
            lifecycle="leave_closed_checkpoint",
        )
    for method in ["identity"] * 5 + ["close"]:
        _submit(scene, method, operation="op-quit", command="session close", lifecycle="empty_quit")
    assert len(list((scene.root / "commands").iterdir())) == 117
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert all(
        json.loads(raw)["payload"]["original_outcome"] == "unknown" for raw in original.values()
    )


def test_claim_cannot_be_stolen_by_another_preview_or_original_id_without_write(
    tmp_path, monkeypatch
):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    _submit(scene, "clone_project", operation="op-edit", command="project create")
    for index in range(3):
        _submit(scene, operation=f"query-{index}")
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(scene, "snapshot", operation="op-not-a-write", command="schematic edit")
    _submit(scene, "snapshot", operation="op-edit", command="project create")
    _submit(
        scene,
        "release_project_preflight",
        operation="op-final",
        command="project backup",
        lifecycle="leave_closed_checkpoint",
    )
    before = len(list((scene.root / "commands").iterdir()))
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(
            scene,
            "release_project_preflight",
            operation="op-other-preview",
            command="project backup",
            lifecycle="leave_closed_checkpoint",
        )
    assert len(list((scene.root / "commands").iterdir())) == before
    _submit(
        scene,
        "release_project",
        operation="op-final",
        command="project backup",
        lifecycle="leave_closed_checkpoint",
    )
    _submit(
        scene, "identity", operation="op-close", command="session close", lifecycle="empty_quit"
    )
    _submit(scene, "close", operation="op-close", command="session close", lifecycle="empty_quit")


@pytest.mark.parametrize(
    "method", ["release_project_preflight", "release_project", "reopen_project"]
)
def test_ordinary_backup_cannot_consume_final_closed_checkpoint_reserve(
    tmp_path, monkeypatch, method
):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    for index in range(4):
        _submit(scene, operation=f"query-{index}")
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(scene, method, operation="op-ordinary-backup", command="project backup")
    assert len(list((scene.root / "commands").iterdir())) == 4


def test_pending_results_are_charged_as_full_slots_not_unobserved_zero(tmp_path, monkeypatch):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path, complete=False)
    reply = _submit(scene, operation="query-pending")
    report = O.observe_owner_capacity(scene.ticket)
    assert report["status"] == "observed"
    assert 0 < report["bytes"]["used"] < O.MAX_COMMAND_SLOT_BYTES
    assert report["bytes"]["committed_including_pending"] == O.MAX_COMMAND_SLOT_BYTES
    assert report["slots"]["used"] == 1
    request = scene.root / "commands" / reply["command_id"] / "request.json"
    raw = request.read_bytes()
    request.write_bytes(raw[:-10])
    unknown = O.observe_owner_capacity(scene.ticket)
    assert unknown["status"] == "unobserved"
    assert unknown["slots"] is unknown["bytes"] is unknown["ordinary_commands_available"] is None


def test_byte_reservation_rejects_before_publication_even_with_free_command_slots(
    tmp_path, monkeypatch
):
    _small(monkeypatch)
    monkeypatch.setattr(O, "MAX_PUBLIC_BYTES", 7 * O.MAX_COMMAND_SLOT_BYTES)
    scene = _context(monkeypatch, tmp_path, complete=False)
    _submit(scene, operation="query-pending")
    before = len(list((scene.root / "commands").iterdir()))
    with pytest.raises(O.OwnerCommandNotPublished) as refusal:
        _submit(scene, operation="query-more")
    assert refusal.value.capacity["slots"]["remaining"] == 9
    assert refusal.value.capacity["ordinary_commands_available"] == 0
    assert len(list((scene.root / "commands").iterdir())) == before


def test_missing_explicit_output_preserves_ordinary_behavior_but_cannot_use_reserve(
    tmp_path, monkeypatch
):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    _submit(scene, "clone_project", operation="op-created", command="project create")
    scope = D.validate_editing_scope(scene.intent["editing_scope"])
    binding = R._binding(
        scope,
        "project backup",
        "op-default",
        options={"project": scene.project, "leave-closed": True},
    )
    assert "lifecycle_intent" not in binding
    O.submit_owner_adapter_command(
        scene.ticket,
        "release_project_preflight",
        {"project": scene.project, "observe_only": True},
        sdk_operation_id="op-default",
        authorization_binding=binding,
    )
    for index in range(2):
        _submit(scene, operation=f"query-{index}")
    with pytest.raises(O.OwnerCommandNotPublished):
        O.submit_owner_adapter_command(
            scene.ticket,
            "release_project_preflight",
            {"project": scene.project, "observe_only": True},
            sdk_operation_id="op-default",
            authorization_binding=binding,
        )
    assert len(list((scene.root / "commands").iterdir())) == 4
    assert O.observe_owner_capacity(scene.ticket)["reserve_claimed"]["closed_checkpoint"] is False


def test_fixed_metadata_bound_covers_initial_and_two_fixed_phase_records():
    request = {
        "command_id": "owner-command-" + "f" * 32,
        "epoch": "e" * 32,
        "sdk_operation_id": "o" * 128,
        "method": max(D.METHODS, key=len),
    }
    key = b"k" * 32
    initial = {
        "command_id": request["command_id"],
        "epoch": request["epoch"],
        "original_outcome": "unknown",
        "immutable": True,
    }
    total = len(O.L._pack(initial, key))
    for index, phase in enumerate(("command_received", "command_started"), 1):
        payload = {
            **{name: request[name] for name in ("command_id", "epoch", "sdk_operation_id")},
            "request_sha256": "a" * 64,
            "sequence": index,
            "previous_sha256": "b" * 64,
            "phase": phase,
            "details": {"command_id": request["command_id"], "method": request["method"]},
        }
        total += len(O.L._pack(payload, key))
    assert total < O.MAX_COMMAND_AUX_BYTES
    assert O.MAX_COMMAND_SLOT_BYTES >= 2 * O.MAX_COMMAND_RECORD_BYTES + total


@pytest.mark.parametrize("operation", ["", "o" * 129, "private/path"])
def test_oversized_or_invalid_sdk_identity_rejected_before_creating_record(
    tmp_path, monkeypatch, operation
):
    scene = _context(monkeypatch, tmp_path)
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(scene, operation=operation)
    assert list((scene.root / "commands").iterdir()) == []


def test_pool_and_counter_are_not_accepted_from_caller_params(tmp_path, monkeypatch):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    for index in range(4):
        _submit(scene, operation=f"query-{index}")
    with pytest.raises(O.OwnerCommandNotPublished):
        _submit(
            scene, params={"capacity": {"pool": "empty_quit", "new_claim": True}, "remaining": 128}
        )
    assert len(list((scene.root / "commands").iterdir())) == 4


def test_lifecycle_binding_is_derived_from_actual_sdk_options_and_stable_revision_target(tmp_path):
    scope_path = tmp_path / "scope.json"
    scope_path.write_bytes(b"offline scope fixture")
    scope = NS(granted=True, scope_id="fixture", grant_file=scope_path)
    options = {
        "leave-closed": True,
        "project": str((tmp_path / "own.prj").resolve()),
        "output": str((tmp_path / "checkpoint.zip").resolve()),
    }
    target = {
        "extensions": {
            "designer_leave_closed_requested": True,
            "path": options["project"],
            "designer_lifecycle_output": options["output"],
        }
    }
    preview = R._binding(scope, "project backup", "op-original", options=options)
    confirm = R._binding(scope, "project backup", "op-original", options=copy.deepcopy(options))
    revision = R._binding(scope, "project backup", "op-original", target=target)
    assert preview == confirm == revision
    assert D.lifecycle_capacity_intent(preview, "release_project_preflight") == "closed_checkpoint"
    assert (
        R._binding(scope, "project backup", "op-other", options={}).get("lifecycle_intent") is None
    )
    with pytest.raises(D.DispatchError):
        D.lifecycle_capacity_intent(preview, "reopen_project")
    with pytest.raises(D.DispatchError):
        D.lifecycle_capacity_intent({**preview, "command": "schematic edit"}, "identity")


def test_real_public_sdk_preview_revision_confirm_share_original_lifecycle_id(
    tmp_path, monkeypatch
):
    from test_backup_preflight_public import scene

    from xpedition_cli.backends.native_xpedition import NativeBackend
    from xpedition_cli.driver import runtime

    args, _report, _calls = scene(tmp_path, monkeypatch)
    fake_invoke = NativeBackend.invoke
    routed = []

    def capture(backend, method, params, **kwargs):
        producer = C._ROUTING.get()
        assert producer is not None
        routed.append((producer[1], copy.deepcopy(producer[2])))
        return fake_invoke(backend, method, params, **kwargs)

    monkeypatch.setattr(NativeBackend, "invoke", capture)
    with runtime(state_dir=tmp_path / "operations") as cli:
        preview = cli.run([*args, "--leave-closed", "--dry-run", "--quiet"])
        assert preview.envelope["ok"], preview.envelope
        operation_id = preview.envelope["data"]["preview"]["operation_id"]
        confirmed = cli.run(
            [
                *args,
                "--leave-closed",
                "--confirm",
                preview.envelope["data"]["confirm_token"],
                "--quiet",
            ]
        )
    assert confirmed.envelope["ok"], confirmed.envelope
    assert len(routed) >= 5
    assert {row[0] for row in routed} == {operation_id}
    assert all(row[1]["source_operation_id"] == operation_id for row in routed)
    assert len({json.dumps(row[1]["lifecycle_intent"], sort_keys=True) for row in routed}) == 1
    assert routed[0][1]["lifecycle_intent"]["output"] == str((tmp_path / "all.zip").resolve())


@pytest.mark.parametrize("pid", [None, 0, "not-a-pid", True])
def test_no_valid_explicit_pid_never_creates_an_invalid_lifecycle_intent(tmp_path, pid):
    path = tmp_path / "scope.json"
    path.write_bytes(b"offline source fixture")
    scope = NS(granted=True, scope_id="fixture", grant_file=path)
    authority = R._binding(
        scope, "session close", "op-close", options={"kind": "schematic", "pid": pid}
    )
    assert "lifecycle_intent" not in authority


def test_public_sdk_no_pid_keeps_existing_usage_refusal_without_any_native_request(
    tmp_path, monkeypatch
):
    from test_driver_v2 import _scope

    from xpedition_cli import driver, session
    from xpedition_cli.backends.native_xpedition import NativeBackend

    _scope(tmp_path, monkeypatch)
    reference = {
        "protocol": C.PROTOCOL,
        "owner_id": "b" * 32,
        "owner_epoch_sha256": "c" * 64,
        "source_bundle_sha256": "d" * 64,
    }
    actual = {"pid": 7777, "creation_time_100ns": "100", "executable": "C:/vendor/viewdraw.exe"}
    # Only the exact old PID argument check is under test. The target and
    # prepared OS registry are explicit offline fixtures; the actual public SDK
    # and unmodified sessions.stop handler execute. No native identity is given.
    monkeypatch.setattr(
        session,
        "read_state",
        lambda: {
            "state": "started",
            "domain": "schematic",
            "owner_ref": reference,
            "process_identity": actual,
        },
    )
    monkeypatch.setattr(R, "prepared_close_observation", lambda *_: None)
    monkeypatch.setattr(NativeBackend, "status", lambda *_: {"available": True})
    monkeypatch.setattr(
        NativeBackend, "invoke", lambda *_a, **_k: pytest.fail("No PID must not invoke CAD")
    )

    class SourceOnlyTarget(driver.Adapter):
        def _target(self, call, command, options):
            path = tmp_path / "no-native-target"
            return {
                "mode": "file",
                "engine_id": "xpedition-cli-files",
                "instance_id": None,
                "object_id": str(path),
                "locator": path.as_uri(),
                "extensions": {
                    "path": str(path),
                    "domain": "schematic",
                    "command": call.command,
                    "input_paths": [],
                },
            }

    with driver.runtime(
        SourceOnlyTarget(NativeBackend()), state_dir=tmp_path / "operations"
    ) as cli:
        result = cli.run(["session", "close", "--kind", "schematic", "--dry-run", "--quiet"])
    assert result.envelope["ok"] is False
    assert result.envelope["error"]["code"] == "E_USAGE", result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"


def _lifecycle_request(scene, *, project=None, pid=None, operation="op-closing", output=None):
    scope = D.validate_editing_scope(scene.intent["editing_scope"])
    command = "session close" if pid is not None else "project backup"
    options = (
        {"pid": pid}
        if pid is not None
        else {"project": project, "leave-closed": True, "output": output or scene.archive}
    )
    binding = R._binding(scope, command, operation, options=options)
    params = (
        {"domain": "schematic"} if pid is not None else {"project": project, "observe_only": True}
    )
    return O.submit_owner_adapter_command(
        scene.ticket,
        "identity" if pid is not None else "release_project_preflight",
        params,
        sdk_operation_id=operation,
        authorization_binding=binding,
        observation_seconds=0,
    )


@pytest.mark.parametrize(
    "fault",
    ["wrong_project", "wrong_pid", "insufficient_scope", "outside_output", "missing_artifact"],
)
def test_bad_lifecycle_preview_cannot_claim_or_consume_preserved_records(
    tmp_path, monkeypatch, fault
):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    if fault != "wrong_pid":
        _submit(scene, "clone_project", operation="op-created", command="project create")
    for index in range(4 - len(list((scene.root / "commands").iterdir()))):
        _submit(scene, operation=f"query-{index}")
    original = {path: path.read_bytes() for path in (scene.root / "commands").rglob("*.json")}
    if fault in {"insufficient_scope", "missing_artifact"}:
        grant = Path(scene.intent["editing_scope"]["scope_file"])
        record = json.loads(grant.read_text(encoding="utf-8"))
        record["effects"].remove("persist" if fault == "insufficient_scope" else "artifact")
        grant.write_text(json.dumps(record), encoding="utf-8")
        scene.intent["editing_scope"]["scope_sha256"] = hashlib.sha256(
            grant.read_bytes()
        ).hexdigest()
    with pytest.raises(O.OwnerCommandNotPublished):
        _lifecycle_request(
            scene,
            project=str((tmp_path / "wrong.prj").resolve())
            if fault == "wrong_project"
            else scene.project,
            pid=7778 if fault == "wrong_pid" else None,
            output=str((tmp_path.parent / "outside-output.zip").resolve())
            if fault == "outside_output"
            else None,
        )
    assert len(list((scene.root / "commands").iterdir())) == 4
    report = O.observe_owner_capacity(scene.ticket)
    assert report["reserve_claimed"]["closed_checkpoint"] is False
    assert report["reserve_claimed"]["empty_quit"] is False
    assert all(path.read_bytes() == raw for path, raw in original.items())
    if fault in {"wrong_project", "outside_output"}:
        _lifecycle_request(scene, project=scene.project, operation="op-correct")
    elif fault == "wrong_pid":
        _lifecycle_request(scene, pid=7777, operation="op-correct")


@pytest.mark.parametrize("empty", [False, None])
def test_latest_open_or_unobserved_document_cannot_fall_back_to_initial_empty(
    tmp_path, monkeypatch, empty
):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    for index in range(3):
        _submit(scene, operation=f"query-{index}")
    scene.application._capacity_document_empty = empty
    _submit(scene, operation="query-latest")
    with pytest.raises(O.OwnerCommandNotPublished, match="exact_empty_target"):
        _lifecycle_request(scene, pid=7777)
    assert len(list((scene.root / "commands").iterdir())) == 4
    assert O.observe_owner_capacity(scene.ticket)["reserve_claimed"]["empty_quit"] is False


@pytest.mark.parametrize("ordinary", [False, True])
def test_same_checkpoint_readonly_after_archive_is_not_a_new_write(tmp_path, monkeypatch, ordinary):
    monkeypatch.setattr(O, "MAX_PUBLIC_COMMANDS", 24)
    monkeypatch.setattr(O, "VERIFICATION_COMMAND_RESERVE", 4)
    scene = _context(monkeypatch, tmp_path)
    _submit(scene, "clone_project", operation="op-created", command="project create")
    if not ordinary:
        for index in range(3):
            _submit(scene, operation=f"query-{index}")
    _lifecycle_request(scene, project=scene.project, operation="op-checkpoint")
    _submit(
        scene,
        "release_project",
        operation="op-checkpoint",
        command="project backup",
        lifecycle="leave_closed_checkpoint",
    )
    Path(scene.archive).write_bytes(b"completed file fixture; no native/export qualification")
    _lifecycle_request(scene, project=scene.project, operation="op-checkpoint")
    before = {path: path.read_bytes() for path in (scene.root / "commands").rglob("*.json")}
    with pytest.raises(O.OwnerCommandNotPublished):
        _lifecycle_request(scene, project=scene.project, operation="op-new")
    other = tmp_path / "other-existing.zip"
    other.write_bytes(b"different target")
    with pytest.raises(O.OwnerCommandNotPublished):
        _lifecycle_request(
            scene, project=scene.project, operation="op-checkpoint", output=str(other)
        )
    with pytest.raises(O.OwnerCommandNotPublished, match="already_published_no_replay"):
        _submit(
            scene,
            "release_project",
            operation="op-checkpoint",
            command="project backup",
            lifecycle="leave_closed_checkpoint",
        )
    assert {path: path.read_bytes() for path in (scene.root / "commands").rglob("*.json")} == before


def test_public_sdk_backup_after_dispatch_observation_passes_real_owner_admission(
    tmp_path, monkeypatch
):
    from xpedition_cli import native_process
    from xpedition_cli.backends.native_xpedition import NativeBackend
    from xpedition_cli.driver import runtime

    monkeypatch.setattr(O, "MAX_PUBLIC_COMMANDS", 24)
    monkeypatch.setattr(O, "VERIFICATION_COMMAND_RESERVE", 4)
    scene = _context(monkeypatch, tmp_path)
    project = Path(scene.project)
    project.parent.mkdir()
    project.write_bytes(b"offline project fixture, no native database")
    _submit(scene, "clone_project", operation="op-created", command="project create")
    for index in range(3):
        _submit(scene, operation=f"query-{index}")
    report = {
        "project": scene.project,
        "board": None,
        "library_state": {"status": "offline owner fixture"},
        "release_preflight": {
            domain: {"processes": [], "application": None, "document": None}
            for domain in ("library", "schematic", "pcb")
        },
        "read_only": True,
        "save_count": 0,
        "close_count": 0,
    }
    scene.responses["release_project_preflight"] = report
    scene.responses["release_project"] = {
        "quiescent": True,
        "designer_closed": True,
        "layout_closed": False,
    }
    reference = {
        "protocol": C.PROTOCOL,
        "owner_id": "b" * 32,
        "owner_epoch_sha256": "c" * 64,
        "source_bundle_sha256": "d" * 64,
    }
    locator = C.OwnerLocator(
        C._SEAL, reference, {"editing_scope": scene.intent["editing_scope"]}, lambda *_: None
    )
    monkeypatch.setattr(C, "_ticket", lambda *_: (O, scene.ticket, scene.root, scene.key))
    monkeypatch.setattr(R, "_provider", lambda *_args, **_kwargs: lambda *_a, **_k: locator)
    monkeypatch.setattr(NativeBackend, "status", lambda *_: {"available": True})
    monkeypatch.setattr(
        NativeBackend, "_command_path", lambda *_: pytest.fail("Unexpected real adapter")
    )
    monkeypatch.setattr(
        native_process,
        "processes",
        lambda domain: [{"pid": 7777, "fixture": True}] if domain == "schematic" else [],
    )
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", scene.intent["editing_scope"]["scope_file"])
    args = [
        "project",
        "backup",
        "--project",
        scene.project,
        "--output",
        scene.archive,
        "--leave-closed",
        "--quiet",
    ]
    with runtime(state_dir=tmp_path / "operations") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        operation_id = preview.envelope["data"]["preview"]["operation_id"]
        confirmed = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert confirmed.envelope["ok"], confirmed.envelope
    assert Path(scene.archive).is_file()
    request_rows = [
        O._command_read(directory / "request.json", scene.key)[0]
        for directory in (scene.root / "commands").iterdir()
    ]
    original = [row for row in request_rows if row["sdk_operation_id"] == operation_id]
    assert sum(row["method"] == "release_project" for row in original) == 1
    assert sum(row["method"] == "release_project_preflight" for row in original) >= 4
    assert all(
        row["authorization_binding"]["source_operation_id"] == operation_id for row in original
    )


def test_pending_write_blocks_claim_from_older_empty_observation(tmp_path, monkeypatch):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path, complete=False)
    first = _submit(scene, "clone_project", operation="op-pending", command="project create")
    for index in range(3):
        _submit(scene, operation=f"query-{index}")
    initial = scene.root / "commands" / first["command_id"] / "initial.json"
    raw = initial.read_bytes()
    with pytest.raises(O.OwnerCommandNotPublished, match="admission_unobserved"):
        _lifecycle_request(scene, pid=7777)
    assert initial.read_bytes() == raw
    assert len(list((scene.root / "commands").iterdir())) == 4


def _client(monkeypatch, scene, *, operation="op-new", binding=None):
    locator = NS(reference={"fixture": "opaque-only"})
    monkeypatch.setattr(C, "_ticket", lambda *_: (O, scene.ticket, None, None))
    monkeypatch.setattr(
        C, "_authorization", lambda *_: (operation, binding or {"command": "schematic edit"})
    )
    monkeypatch.setattr(
        C,
        "_service_observation",
        lambda *_: {"phase": "public_dispatch_ready", "owner_state": "verified_alive"},
    )
    return locator


def test_client_reports_proved_not_published_without_creating_unknown(tmp_path, monkeypatch):
    _small(monkeypatch)
    scene = _context(monkeypatch, tmp_path)
    for index in range(4):
        _submit(scene, operation=f"query-{index}")
    locator = _client(monkeypatch, scene)
    with pytest.raises(CLIError) as failure:
        C.call_owner(locator, "apply_changeset", {}, 1)
    assert failure.value.code == "E_CONFLICT"
    details = failure.value.details
    assert details["owner_command_id"] is None
    assert details["dispatch_state"] == "not_started"
    assert (
        details["write_attempted"]
        is details["native_request_dispatched"]
        is details["owner_request_published"]
        is False
    )
    assert len(list((scene.root / "commands").iterdir())) == 4


@pytest.mark.parametrize("phase", ["partial_publication", "observation", "generic_untyped_refusal"])
def test_ambiguous_boundaries_keep_original_unknown_and_id_when_available(
    tmp_path, monkeypatch, phase
):
    scene = _context(monkeypatch, tmp_path)
    locator = _client(monkeypatch, scene)
    if phase == "partial_publication":
        monkeypatch.setattr(
            O, "_command_write", lambda *_: (_ for _ in ()).throw(OSError("private failure"))
        )
    elif phase == "observation":
        monkeypatch.setattr(
            O,
            "observe_owner_command",
            lambda *_a, **_k: (_ for _ in ()).throw(ValueError("private failure")),
        )
    else:
        monkeypatch.setattr(
            O,
            "submit_owner_adapter_command",
            lambda *_a, **_k: (_ for _ in ()).throw(ValueError("private failure")),
        )
    with pytest.raises(CLIError) as failure:
        C.call_owner(locator, "apply_changeset", {}, 1)
    assert failure.value.code == "E_OUTCOME_UNKNOWN"
    assert "private failure" not in json.dumps(failure.value.details)
    directories = list((scene.root / "commands").iterdir())
    if phase != "generic_untyped_refusal":
        assert failure.value.details["owner_command_id"] == directories[0].name
        initial, _ = O._command_read(directories[0] / "initial.json", scene.key)
        assert initial["original_outcome"] == "unknown"
    assert failure.value.details["owner_request_published"] is (
        True if phase == "observation" else None
    )


@pytest.mark.parametrize("case", ["capacity", "partial", "observation", "lock_enter", "lock_exit"])
def test_real_subprocess_response_and_retained_unknown(tmp_path, case):
    if case == "lock_enter" and os.name != "nt":
        pytest.skip("real bounded msvcrt lock contention is Windows-only")
    process = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(Path(__file__).resolve()),
            "--child",
            case,
            str(tmp_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["native_calls"] == 0
    assert result["automatic_replay"] is False
    if case in {"capacity", "lock_enter"}:
        assert result["code"] == "E_CONFLICT" and result["published"] is False
        assert result["records"] == (4 if case == "capacity" else 0)
    else:
        assert result["code"] == "E_OUTCOME_UNKNOWN"
        assert result["original_outcome"] == "unknown"
        assert result["retained_id"] is True
        # The caller process has exited. A different real process reads only the
        # same authenticated original record; it never publishes another call.
        initial = tmp_path / "owner" / "commands" / result["owner_command_id"] / "initial.json"
        original = initial.read_bytes()
        code = (
            "import json,sys; from pathlib import Path; "
            "from xpedition_cli import designer_native_owner as O; "
            "value,_=O._command_read(Path(sys.argv[1]), b'k'*32); "
            "print(json.dumps({'original_outcome':value['original_outcome']}))"
        )
        observer = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-c", code, str(initial)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=True,
        )
        assert json.loads(observer.stdout)["original_outcome"] == "unknown"
        assert initial.read_bytes() == original
        assert len(list((tmp_path / "owner" / "commands").iterdir())) == 1


def _child(case, temporary):
    # Actual process/file/MAC/error boundaries; all application/process facts
    # are declared fixtures. Native API access is a hard test failure.
    import ctypes
    from contextlib import contextmanager

    from xpedition_cli.backends.native_xpedition import NativeBackend

    native_hits = []
    holder = None
    real_lock = O.L._launch_lock
    with pytest.MonkeyPatch.context() as monkeypatch:

        def forbidden(*_args, **_kwargs):
            native_hits.append(True)
            raise AssertionError("unexpected CAD/native access")

        monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
        monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
        monkeypatch.setattr(NativeBackend, "_command_path", forbidden)
        scene = _context(monkeypatch, temporary)
        locator = _client(monkeypatch, scene)
        if case == "capacity":
            _small(monkeypatch)
            for index in range(4):
                _submit(scene, operation=f"query-{index}")
        elif case == "partial":
            monkeypatch.setattr(
                O,
                "_command_write",
                lambda *_: (_ for _ in ()).throw(OSError("simulated publication boundary")),
            )
        elif case == "observation":
            monkeypatch.setattr(
                O,
                "observe_owner_command",
                lambda *_a, **_k: (_ for _ in ()).throw(ValueError("simulated observer boundary")),
            )
        elif case == "lock_enter":
            monkeypatch.setattr(O.L, "_launch_lock", real_lock)
            code = (
                "import sys,time; from pathlib import Path; "
                "from xpedition_cli import execution_lifecycle as L; "
                "root=Path(sys.argv[1]); "
                "lock=L._launch_lock(root); lock.__enter__(); "
                "print('ready',flush=True); sys.stdin.read(1); "
                "lock.__exit__(None,None,None)"
            )
            holder = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-B", "-c", code, str(scene.root)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            assert holder.stdout.readline().strip() == "ready"
        elif case == "lock_exit":

            @contextmanager
            def exit_failure(*_):
                yield
                raise OSError("simulated lock release boundary")

            monkeypatch.setattr(O.L, "_launch_lock", exit_failure)
        else:
            raise AssertionError("unreviewed child case")
        try:
            C.call_owner(locator, "apply_changeset", {}, 2)
        except CLIError as error:
            result = {
                "code": error.code,
                "published": error.details.get("owner_request_published"),
                "owner_command_id": error.details.get("owner_command_id"),
                "automatic_replay": False,
            }
        else:
            raise AssertionError("expected bounded failure")
        finally:
            if holder is not None:
                holder.stdin.write("x")
                holder.stdin.flush()
                holder.stdin.close()
                holder.wait(timeout=5)
                assert holder.returncode == 0
        directories = list((scene.root / "commands").iterdir())
        result["native_calls"] = len(native_hits)
        result["records"] = len(directories)
        if case not in {"capacity", "lock_enter"}:
            directory = directories[0]
            original = (directory / "initial.json").read_bytes()
            value, _ = O._command_read(directory / "initial.json", scene.key)
            result.update(
                original_outcome=value["original_outcome"],
                retained_id=result["owner_command_id"] == directory.name,
            )
            assert (directory / "initial.json").read_bytes() == original
        print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--child":
        raise SystemExit(2)
    _child(sys.argv[2], Path(sys.argv[3]))
