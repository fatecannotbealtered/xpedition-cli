"""Real temporary SDK copy receipts, synthetic project bytes, hard no-CAD boundaries."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_component_copy_origin import _args, file_only  # noqa: F401 - normal SDK copy fixture
from test_driver_v2 import _scope
from test_project_isolated_copy import fixture as source_fixture

from xpedition_cli import designer_first_open as F
from xpedition_cli._vendor.tool_driver_sdk import validation as V
from xpedition_cli.audit import config_dir
from xpedition_cli.backends import NativeBackend
from xpedition_cli.driver import runtime
from xpedition_cli.errors import CLIError

OPEN_ID = "op-" + "f" * 32
EPOCH_SHA = "e" * 64


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        raise AssertionError("First-open source tests may not use a real process/COM API")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden, raising=False)
    monkeypatch.setattr(F.P, "processes", forbidden)
    monkeypatch.setattr(F.W, "process_generation", forbidden)
    yield
    assert hits == []


@pytest.fixture
def source(tmp_path, monkeypatch, file_only):  # noqa: F811 - explicit fake transport fixture
    _scope(tmp_path, monkeypatch)
    template, archive, target, files = source_fixture(tmp_path)
    args = _args(template, archive, target)
    with runtime() as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        complete = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert complete.envelope["ok"], complete.envelope
        validator = cli.validator
        receipt = complete.envelope["data"]["receipt"]
    # Reuse the real immutable specification validator within this one fresh
    # fixture; every plan/receipt/scope validation still executes normally.
    monkeypatch.setattr(F, "ContractValidator", lambda _root: validator)
    monkeypatch.setattr(V, "ContractValidator", lambda _root: validator)
    copied = {
        "source": template,
        "archive": archive,
        "archive_bytes": archive.read_bytes(),
        "target": target,
        "files": files,
        "receipt": receipt,
        "native_calls": file_only,
    }
    grant = Path(os.environ["XPEDITION_CLI_SCOPE_FILE"])
    editing = {
        "scope_file": str(grant),
        "scope_sha256": hashlib.sha256(grant.read_bytes()).hexdigest(),
        "scope_id": json.loads(grant.read_text())["scope_id"],
    }
    worker = {
        "pid": 103,
        "executable": "C:/fixture/SDD_HOME/wv/win64/bin/viewdraw.exe",
        "creation_time_100ns": "30",
    }
    launcher = {
        "pid": 102,
        "executable": "C:/fixture/SDD_HOME/common/win64/bin/viewdraw.exe",
        "creation_time_100ns": "20",
    }
    own = [
        {"role": "worker", "domain": "schematic", "process_identity": worker, "alive": True},
        {"role": "launcher", "domain": "schematic", "process_identity": launcher, "alive": True},
    ]
    rows = {
        "schematic": [
            {
                "pid": row["process_identity"]["pid"],
                "parent_pid": 102 if row["role"] == "worker" else 1,
                "executable": row["process_identity"]["executable"],
            }
            for row in own
        ],
        "pcb": [],
        "library": [],
    }
    generations = {
        row["process_identity"]["pid"]: {
            "pid": row["process_identity"]["pid"],
            "executable": row["process_identity"]["executable"],
            "birth_filetime_100ns": row["process_identity"]["creation_time_100ns"],
            "alive": True,
        }
        for row in own
    }
    monkeypatch.setattr(F.P, "processes", lambda domain: copy.deepcopy(rows[domain]))
    monkeypatch.setattr(F.W, "process_generation", lambda pid: copy.deepcopy(generations[pid]))
    return {
        **copied,
        "editing": editing,
        "own": own,
        "rows": rows,
        "generations": generations,
        "kwargs": {
            "project": str(copied["target"]),
            "source_operation_id": copied["receipt"]["operation_id"],
            "editing_scope": editing,
            "sdk_operation_id": OPEN_ID,
            "owner_epoch_sha256": EPOCH_SHA,
            "trusted_config_root": config_dir(),
            "own_generation_reader": lambda: copy.deepcopy(own),
        },
    }


def _build(source):
    return F.build_closed_copy_open_binding(**source["kwargs"])


def _verify(source, capsule, **changes):
    values = {key: value for key, value in source["kwargs"].items() if key != "source_operation_id"}
    return F.verify_closed_copy_open_binding(capsule, **{**values, **changes})


def _mutate_sdk(source, *, record_changes=None, mutate_receipt=None):
    path = config_dir() / "driver-v2/operations.sqlite3"
    with sqlite3.connect(path) as connection:
        if mutate_receipt:
            receipt = copy.deepcopy(source["receipt"])
            mutate_receipt(receipt)
            connection.execute(
                "UPDATE operations SET receipt=? WHERE operation_id=?",
                (json.dumps(receipt), source["receipt"]["operation_id"]),
            )
        for key, value in (record_changes or {}).items():
            assert key in {"status", "scope_id", "command"}
            connection.execute(
                f"UPDATE operations SET {key}=? WHERE operation_id=?",
                (value, source["receipt"]["operation_id"]),
            )


def test_public_closed_copy_can_be_independently_bound_but_does_not_open_cad(source):
    capsule = _build(source)
    assert set(capsule) == F._FIELDS
    assert not any(key in capsule for key in ("verified", "sdk_path", "root", "epoch", "endpoint"))
    proof = _verify(source, capsule)
    assert type(proof) is F.VerifiedClosedCopyOpen
    assert proof.project == str(source["target"])
    assert proof.safe_binding == capsule
    derived = proof.safe_binding
    derived["project"] = "tampered caller copy"
    assert proof.project == str(source["target"])
    assert source["native_calls"] == []
    assert source["archive"].read_bytes() == source["archive_bytes"]
    with pytest.raises(CLIError):
        F.VerifiedClosedCopyOpen(None, capsule)


@pytest.mark.parametrize(
    "bad", ["verified_flag", "owner_epoch", "open_operation", "scope", "receipt_hash", "budget"]
)
def test_capsule_cannot_replace_owner_scope_operation_or_original_receipt(source, bad):
    capsule = _build(source)
    if bad == "verified_flag":
        capsule["verified"] = True
    elif bad == "budget":
        capsule["observation_budget"]["total_seconds"] = 9000
        with pytest.raises(CLIError) as failure:
            _verify(source, capsule)
        assert (
            failure.value.details["first_open_observation"]["failed_phase"] == "capsule_comparison"
        )
        assert F.last_observation_report()["status"] == "refused"
        return
    else:
        field, value = {
            "owner_epoch": ("owner_epoch_sha256", "a" * 64),
            "open_operation": ("open_operation_id", "op-other-open"),
            "scope": ("scope_id", "another-scope"),
            "receipt_hash": ("copy_receipt_sha256", "b" * 64),
        }[bad]
        capsule[field] = value
    with pytest.raises(CLIError):
        _verify(source, capsule)


@pytest.mark.parametrize(
    "bad", ["unknown", "foreign_scope", "other_source_command", "native_calls", "genesis"]
)
def test_non_successful_foreign_or_not_closed_copy_sdk_source_is_rejected(source, bad):
    if bad == "unknown":
        _mutate_sdk(source, record_changes={"status": "unknown"})
    elif bad == "foreign_scope":
        _mutate_sdk(source, record_changes={"scope_id": "foreign"})
    elif bad == "other_source_command":
        _mutate_sdk(source, record_changes={"command": "project restore"})
    elif bad == "native_calls":
        _mutate_sdk(
            source,
            mutate_receipt=lambda r: r["extensions"]["result"].update(native_calls_dispatched=1),
        )
    else:
        _mutate_sdk(
            source,
            mutate_receipt=lambda r: r["extensions"]["result"]["closed_copy"]["genesis"][
                "file_manifest"
            ].update({"old.bak": "0" * 64}),
        )
    with pytest.raises(CLIError) as caught:
        _build(source)
    if bad == "other_source_command":
        assert caught.value.code == "E_UNSUPPORTED"


@pytest.mark.parametrize("bad", ["changed", "extra", "missing", "hardlink"])
def test_whole_target_genesis_and_physical_independence_are_mandatory(source, bad):
    root = source["target"].parent
    if bad == "changed":
        (root / "database/icdb.dat").write_bytes(b"changed target bytes")
    elif bad == "extra":
        (root / "extra.txt").write_bytes(b"non-target material")
    elif bad == "missing":
        (root / "Library/SymbolLibs/Globals/global.1").unlink()
    else:
        target = root / "old.bak"
        target.unlink()
        os.link(source["source"].parent / "old.bak", target)
    with pytest.raises(CLIError):
        _build(source)


@pytest.mark.parametrize("bad", ["extra_layout", "reused_worker", "missing_worker", "wrong_image"])
def test_only_original_held_owner_generations_may_be_in_complete_writer_scene(source, bad):
    if bad == "extra_layout":
        source["rows"]["pcb"] = [
            {"pid": 404, "parent_pid": 1, "executable": "C:/fixture/ExpeditionPCB.exe"}
        ]
        source["generations"][404] = {
            "pid": 404,
            "birth_filetime_100ns": "40",
            "alive": True,
            "executable": "C:/fixture/ExpeditionPCB.exe",
        }
    elif bad == "reused_worker":
        source["generations"][103]["birth_filetime_100ns"] = "99"
    elif bad == "missing_worker":
        source["rows"]["schematic"] = [
            row for row in source["rows"]["schematic"] if row["pid"] != 103
        ]
    else:
        source["generations"][103]["executable"] = "C:/other/viewdraw.exe"
    with pytest.raises(CLIError):
        _build(source)


def test_ended_original_launcher_is_not_required_to_be_live(source):
    source["own"][1]["alive"] = False
    source["rows"]["schematic"] = [row for row in source["rows"]["schematic"] if row["pid"] != 102]
    scene = F.observe_owned_designer_scene(
        own_generation_reader=source["kwargs"]["own_generation_reader"]
    )
    assert [row["pid"] for row in scene["allowed_live_generations"]] == [103]
    assert scene["layout"] == []


def test_any_library_manager_writer_blocks_first_open_without_com(source):
    image = "C:/fixture/LibraryManager.exe"
    source["rows"]["library"] = [{"pid": 405, "parent_pid": 1, "executable": image}]
    source["generations"][405] = {
        "pid": 405,
        "birth_filetime_100ns": "41",
        "alive": True,
        "executable": image,
    }
    with pytest.raises(CLIError):
        _build(source)


def test_owner_revalidation_detects_changes_after_frontend_capsule(source):
    capsule = _build(source)
    (source["target"].parent / "database/icdb.dat").write_bytes(b"later mutation")
    with pytest.raises(CLIError):
        _verify(source, capsule)


def test_project_text_limit_refuses_before_loading_large_project(source):
    with source["target"].open("r+b") as stream:
        stream.truncate(F.MAX_PROJECT_BYTES + 1)
    with pytest.raises(CLIError) as caught:
        _build(source)
    assert caught.value.details["reason"] == "first_open_project_text_byte_limit"


def _unresolved_copy_plan(source, *, same_target=False, foreign_scope=False):
    other = source["target"].parent.parent / "other-unresolved-project" / source["target"].name
    with runtime() as cli:
        result = cli.run([*_args(source["source"], source["archive"], other), "--dry-run"])
        assert result.envelope["ok"], result.envelope
        operation_id = result.envelope["data"]["preview"]["operation_id"]
    path = config_dir() / "driver-v2/operations.sqlite3"
    with sqlite3.connect(path) as connection:
        value = json.loads(
            connection.execute(
                "SELECT plan FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()[0]
        )
        if same_target:

            def replace(item):
                if isinstance(item, dict):
                    return {key: replace(value) for key, value in item.items()}
                if isinstance(item, list):
                    return [replace(value) for value in item]
                if isinstance(item, str):
                    return item.replace(str(other.parent), str(source["target"].parent)).replace(
                        other.parent.as_uri(), source["target"].parent.as_uri()
                    )
                return item

            value = replace(value)
        if foreign_scope:
            value["authorization_scope_id"] = "another-task-scope"
        scope_id = value["authorization_scope_id"]
        connection.execute(
            "UPDATE operations SET status='unknown',receipt=NULL,scope_id=?,plan=?,target_key=? "
            "WHERE operation_id=?",
            (scope_id, json.dumps(value), F.target_key(value["target"]), operation_id),
        )
    return operation_id


@pytest.mark.parametrize("foreign_scope", [False, True])
def test_same_physical_target_unknown_blocks_first_open_even_across_scope(source, foreign_scope):
    operation_id = _unresolved_copy_plan(source, same_target=True, foreign_scope=foreign_scope)
    with pytest.raises(CLIError) as caught:
        _build(source)
    assert caught.value.code == "E_CONFLICT"
    with sqlite3.connect(config_dir() / "driver-v2/operations.sqlite3") as connection:
        assert connection.execute(
            "SELECT status,resolution FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone() == ("unknown", None)


def test_complete_unrelated_physical_target_unknown_is_retained_not_resolved(source):
    operation_id = _unresolved_copy_plan(source)
    capsule = _build(source)
    assert capsule["project"] == str(source["target"])
    with sqlite3.connect(config_dir() / "driver-v2/operations.sqlite3") as connection:
        assert connection.execute(
            "SELECT status,resolution FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone() == ("unknown", None)


@pytest.fixture
def checkpoint(source, monkeypatch):
    # Synthetic edits are ordinary temp bytes, never evidence of CAD editing.
    (source["target"].parent / "database/icdb.dat").write_bytes(
        b"synthetic edited checkpoint database"
    )
    # project_backup's all-files format does not serialize empty directories.
    (source["target"].parent / "Work/native-empty-cache").mkdir()
    output = source["target"].parent.parent / "after-edit-closed.zip"
    original_rows = copy.deepcopy(source["rows"])
    for domain in source["rows"]:
        source["rows"][domain] = []
    with monkeypatch.context() as temporary:
        temporary.setattr(
            NativeBackend,
            "status",
            lambda self: {"available": False, "reason": "offline fixture; no CAD processes"},
        )
        with runtime() as cli:
            args = [
                "project",
                "backup",
                "--project",
                str(source["target"]),
                "--output",
                str(output),
                "--leave-closed",
            ]
            preview = cli.run([*args, "--dry-run"])
            assert preview.envelope["ok"], preview.envelope
            complete = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
            assert complete.envelope["ok"], complete.envelope
            receipt = complete.envelope["data"]["receipt"]
    source["rows"].update(original_rows)
    return {
        **source,
        "receipt": receipt,
        "checkpoint_archive": output,
        "kwargs": {**source["kwargs"], "source_operation_id": receipt["operation_id"]},
    }


def test_schema_checkpoint_accepts_edited_bytes_without_claiming_native_save(
    checkpoint,
):
    result = checkpoint["receipt"]["extensions"]["result"]
    assert result["requested_leave_closed"] is result["actual_closed_without_reopen"] is True
    assert result["release"].get("designer_closed") is not True
    assert result["release"]["release_preflight"]["save_count"] == 0
    assert result["release"]["release_preflight"]["close_count"] == 0
    before = copy.deepcopy(checkpoint["receipt"])
    capsule = _build(checkpoint)
    assert capsule["source_kind"] == "closed_checkpoint"
    assert capsule["protocol"] == F.PROTOCOL
    proof = _verify(checkpoint, capsule)
    assert proof.project == str(checkpoint["target"])
    assert "created" not in proof.safe_binding and "save_ran" not in proof.safe_binding
    assert checkpoint["native_calls"] == []
    assert checkpoint["receipt"] == before
    assert before["outcome"]["verification"]["status"] == "not_applicable"


@pytest.mark.parametrize(
    "bad",
    [
        "not_leave_closed",
        "reopened",
        "unknown",
        "foreign_scope",
        "other_project",
        "archive_tampered",
        "missing_archive_pin",
    ],
)
def test_checkpoint_selector_scope_identity_archive_or_unknown_refuses(checkpoint, bad):
    if bad == "not_leave_closed":
        _mutate_sdk(
            checkpoint,
            mutate_receipt=lambda r: r["extensions"]["result"].update(requested_leave_closed=False),
        )
    elif bad == "reopened":
        _mutate_sdk(
            checkpoint,
            mutate_receipt=lambda r: r["extensions"]["result"].update(
                actual_closed_without_reopen=False, closed_and_reopened=True
            ),
        )
    elif bad == "unknown":
        _mutate_sdk(checkpoint, record_changes={"status": "unknown"})
    elif bad == "foreign_scope":
        _mutate_sdk(checkpoint, record_changes={"scope_id": "other-scope"})
    elif bad == "other_project":
        _mutate_sdk(
            checkpoint,
            mutate_receipt=lambda r: r["extensions"]["result"].update(
                project=str(checkpoint["source"])
            ),
        )
    elif bad == "archive_tampered":
        archive = checkpoint["checkpoint_archive"]
        with zipfile.ZipFile(archive) as bundle:
            members = {row.filename: bundle.read(row.filename) for row in bundle.infolist()}
        metadata = json.loads(members[F.B.MANIFEST])
        metadata["sha256"]["database/icdb.dat"] = "0" * 64
        members[F.B.MANIFEST] = json.dumps(metadata).encode()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, raw in members.items():
                bundle.writestr(name, raw)
        # Even a structurally successful source receipt/artifact hash cannot
        # make a producer's false member manifest authoritative.
        observed = hashlib.sha256(archive.read_bytes()).hexdigest()
        _mutate_sdk(checkpoint, mutate_receipt=lambda r: r["artifacts"][0].update(sha256=observed))
    else:
        _mutate_sdk(checkpoint, mutate_receipt=lambda r: r.update(artifacts=[]))
    with pytest.raises(CLIError):
        _build(checkpoint)


def test_checkpoint_cannot_trust_source_kind_json_or_changed_current_tree(checkpoint):
    capsule = _build(checkpoint)
    capsule["source_kind"] = "closed_copy"
    with pytest.raises(CLIError):
        _verify(checkpoint, capsule)
    capsule = _build(checkpoint)
    (checkpoint["target"].parent / "extra-after-checkpoint.txt").write_bytes(b"later bytes")
    with pytest.raises(CLIError):
        _verify(checkpoint, capsule)


def _unknown_first_open(source, *, legacy=False):
    """A real SDK closed copy plus schema-valid fake-native first-open unknown."""
    old = source["target"].parent.parent / "old-open-project" / source["target"].name
    with runtime() as cli:
        args = _args(source["source"], source["archive"], old)
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        copied = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
        assert copied.envelope["ok"], copied.envelope
        copy_id = copied.envelope["data"]["receipt"]["operation_id"]
    operation_id = "op-" + "9" * 32
    capsule = F.build_closed_copy_open_binding(
        **{
            **source["kwargs"],
            "project": str(old),
            "source_operation_id": copy_id,
            "sdk_operation_id": operation_id,
        }
    )
    if legacy is True:
        capsule.pop("source_kind")
        capsule.pop("observation_budget")
        capsule["protocol"] = "designer-closed-copy-first-open-v1"
    elif legacy == "v2_without_budget":
        capsule.pop("observation_budget")
    actual = {
        "pid": 903,
        "executable": "C:/fixture/SDD_HOME/wv/win64/bin/viewdraw.exe",
        "creation_time_100ns": "134375464979013265",
    }
    application = {
        "pid": actual["pid"],
        "domain": "schematic",
        "public_fields": {"FullName": actual["executable"]},
        "document": {"open": False, "path": None, "status": "observed"},
    }
    with F._sdk_reader(config_dir()) as (store, validator):
        record = store.get(copy_id)
        plan = copy.deepcopy(record["plan"])
        plan.update(
            operation_id=operation_id,
            plan_id="plan-" + "9" * 32,
            capability_id=F._declarations()["session attach"]["driver"]["capability_id"],
            effects=["session", "persist"],
        )
        plan["target"] = {
            "mode": "session",
            "engine_id": "xpedition-schematic",
            "instance_id": str(actual["pid"]),
            "object_id": str(old),
            "locator": old.as_uri(),
            "extensions": {
                "command": "session attach",
                "domain": "schematic",
                "project": str(old),
                "source_operation_id": operation_id,
                "process_identity": actual,
                "application": copy.deepcopy(application),
                "designer_project_open_route": "owned-registered-project-first-open-v1",
                "designer_project_source": capsule,
            },
        }
        plan["base_snapshot"]["source"] = "live"
        plan["base_snapshot"]["extensions"] = {
            "facts": {"application": application, "os_process_identity": actual}
        }
        validator.validate("plan", plan)
        receipt = copy.deepcopy(record["receipt"])
        receipt.update(
            operation_id=operation_id,
            plan_id=plan["plan_id"],
            capability_id=plan["capability_id"],
            effects=plan["effects"],
            target=plan["target"],
            snapshot_before=plan["base_snapshot"],
            snapshot_after=None,
            artifacts=[],
            changed_items=[],
            failed_stage="apply",
        )
        receipt["outcome"].update(execution="failed", change="unknown", persistence="unknown")
        receipt["outcome"]["verification"].update(
            status="incomplete",
            snapshot_id=None,
            coverage="none",
            checks=[],
            evidence_refs=[],
            not_checked=[],
        )
        receipt["extensions"] = {"fixture": "No native first open was executed"}
        validator.validate("receipt", receipt)
    with sqlite3.connect(config_dir() / "driver-v2/operations.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        row = dict(
            connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (copy_id,)
            ).fetchone()
        )
        row.update(
            operation_id=operation_id,
            plan_id=plan["plan_id"],
            capability_id=plan["capability_id"],
            command="session attach",
            status="unknown",
            target_key=F.target_key(plan["target"]),
            effects=json.dumps(plan["effects"]),
            plan=json.dumps(plan),
            receipt=json.dumps(receipt),
            resolution=None,
        )
        columns = list(row)
        connection.execute(
            "INSERT INTO operations ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            [row[key] for key in columns],
        )
    return operation_id, old, plan


@pytest.mark.parametrize("legacy", [False, True, "v2_without_budget"])
def test_empty_first_open_unknown_only_protects_its_declared_physical_copy(source, legacy):
    operation_id, old, plan = _unknown_first_open(source, legacy=legacy)
    capsule = _build(source)
    assert capsule["project"] == str(source["target"])
    with sqlite3.connect(config_dir() / "driver-v2/operations.sqlite3") as connection:
        row = connection.execute(
            "SELECT status,resolution,plan FROM operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
    assert row[0:2] == ("unknown", None)
    assert json.loads(row[2]) == plan and old != source["target"]


@pytest.mark.parametrize(
    "bad",
    [
        "same_path",
        "physical_file_alias",
        "physical_directory_alias",
        "missing_birth",
        "bad_role",
        "bad_hash",
    ],
)
def test_first_open_unknown_same_resource_or_malformed_declaration_still_blocks(source, bad):
    operation_id, _old, plan = _unknown_first_open(source)
    extension = plan["target"]["extensions"]
    capsule = extension["designer_project_source"]
    if bad == "same_path":
        project = source["target"]
        plan["target"].update(object_id=str(project), locator=project.as_uri())
        extension["project"] = capsule["project"] = str(project)
    elif bad == "physical_file_alias":
        capsule["target_project_identity"] = F._identity(source["target"])
    elif bad == "physical_directory_alias":
        capsule["target_directory_identity"] = F._identity(source["target"].parent)
    elif bad == "missing_birth":
        extension["process_identity"].pop("creation_time_100ns")
    elif bad == "bad_role":
        extension["domain"] = "pcb"
    else:
        capsule["copy_receipt_sha256"] = "unverified"
    with sqlite3.connect(config_dir() / "driver-v2/operations.sqlite3") as connection:
        receipt = json.loads(
            connection.execute(
                "SELECT receipt FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()[0]
        )
        receipt["target"] = plan["target"]
        receipt["snapshot_before"] = plan["base_snapshot"]
        connection.execute(
            "UPDATE operations SET plan=?,receipt=?,target_key=? WHERE operation_id=?",
            (json.dumps(plan), json.dumps(receipt), F.target_key(plan["target"]), operation_id),
        )
    with pytest.raises(CLIError):
        _build(source)


@pytest.mark.parametrize("kind,seconds", [("closed_copy", 86.0), ("closed_checkpoint", 62.0)])
def test_fixed_budget_profile_covers_representative_work_with_a_hard_ceiling(kind, seconds):
    budget = F.observation_budget_for(kind, 3091, 583185100)
    assert budget["profile"] == F.BUDGET_PROFILE
    assert budget["inputs"] == {"files": 3091, "project_bytes": 583185100}
    assert budget["total_seconds"] == seconds <= F.MAX_SECONDS == 90
    maximum = F.observation_budget_for(kind, F.I.MAX_FILES, F.I.MAX_EXPANDED_BYTES)
    assert maximum["total_seconds"] == 90
    assert F.observation_budget_for(kind, 2, 40)["total_seconds"] == F.MIN_SECONDS == 30


@pytest.mark.parametrize(
    "kind,files,size",
    [
        ("unverified", 2, 40),
        ("closed_copy", True, 40),
        ("closed_checkpoint", 0, 40),
        ("closed_copy", F.I.MAX_FILES + 1, 40),
        ("closed_copy", 2, True),
        ("closed_checkpoint", 2, -1),
        ("closed_checkpoint", 2, F.I.MAX_EXPANDED_BYTES + 1),
    ],
)
def test_budget_metadata_cannot_expand_resource_limits(kind, files, size):
    with pytest.raises(CLIError) as failure:
        F.observation_budget_for(kind, files, size)
    assert failure.value.details["reason"] == "first_open_budget_input_resource_limit"
    assert failure.value.details["native_request_dispatched"] is False


def test_elapsed_and_phase_reports_do_not_change_the_frontend_owner_binding(source, monkeypatch):
    clock, cost = [0.0], [1.0]
    monkeypatch.setattr(F, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = F._tree

    def measured_tree(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += cost[0]
        return result

    monkeypatch.setattr(F, "_tree", measured_tree)
    first = _build(source)
    report = F.last_observation_report()
    assert report["elapsed_seconds"] == 2.0
    assert report["status"] == "completed" and report["failed_phase"] is None
    assert "elapsed_seconds" not in first and "phase_elapsed_seconds" not in first
    report["budget"]["total_seconds"] = 9000
    assert F.last_observation_report()["budget"] == first["observation_budget"]
    clock[0], cost[0] = 0.0, 3.0
    second = _build(source)
    assert F.last_observation_report()["elapsed_seconds"] == 6.0
    assert first == second
    clock[0], cost[0] = 0.0, 2.0
    assert _verify(source, first).safe_binding == first
    assert F.last_observation_report()["elapsed_seconds"] == 4.0
    assert source["native_calls"] == []


def test_budget_exhaustion_reports_exact_phase_without_dispatch_or_record_rewrite(
    source,
    monkeypatch,
):
    clock = [0.0]
    monkeypatch.setattr(F, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = F._tree

    def expired_tree(*args, **kwargs):
        clock[0] = 91.0
        return original(*args, **kwargs)

    monkeypatch.setattr(F, "_tree", expired_tree)
    before = copy.deepcopy(source["receipt"])
    with pytest.raises(CLIError) as failure:
        _build(source)
    details = failure.value.details
    assert details["reason"] == "target_observation_budget_exhausted"
    assert details["write_attempted"] is details["native_request_dispatched"] is False
    observation = details["first_open_observation"]
    assert observation["failed_phase"] == "target_tree_initial"
    assert observation["elapsed_seconds"] == 91.0
    assert observation["budget"]["total_seconds"] <= F.MAX_SECONDS
    assert F.last_observation_report()["failed_phase"] == "target_tree_initial"
    with F._sdk_reader(config_dir()) as (store, _validator):
        assert store.get(before["operation_id"])["receipt"] == before
    assert source["native_calls"] == []


def test_budget_hint_does_not_replace_actual_complete_target_measurement(source):
    def inflate_hint(receipt):
        receipt["extensions"]["result"]["closed_copy"]["written_files"][0]["bytes"] += 1

    _mutate_sdk(source, mutate_receipt=inflate_hint)
    with pytest.raises(CLIError) as failure:
        _build(source)
    assert failure.value.details["reason"] == "source_budget_inputs_disagree_with_complete_target"
    assert failure.value.details["native_request_dispatched"] is False


def test_historical_budgetless_capsule_never_becomes_a_current_open_capability(source):
    capsule = _build(source)
    capsule.pop("observation_budget")
    with pytest.raises(CLIError) as failure:
        _verify(source, capsule)
    assert failure.value.details["reason"] == "exact_first_open_capsule_required"
